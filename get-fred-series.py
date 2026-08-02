#!/usr/bin/env python3
"""Fetch the metadata of every FRED series, category by category.

FRED publishes hundreds of thousands of series but has no endpoint that lists
them. fred/category/series returns the series filed under one category, so this
walks the category tree produced by get-fred-id.py and pages through each one.
A full run measured 845,501 series over 5,582 requests in 86 minutes; a page of
1,000 series is most of a megabyte, so the crawl is bound by transfer rather
than by the rate limit and raising --rate does little.

Run get-fred-id.py first; this script reads its combined CSV to know which
categories exist.

Series belong to several categories at once -- 1,043,049 pairings for those
845,501 series -- so the output is normalised into the series themselves and
the pairings between them and categories.

    export FRED_API_KEY=...
    python get-fred-series.py                  # everything, about 90 minutes
    python get-fred-series.py --root 32991     # one branch, minutes
    python get-fred-series.py --format sqlite  # one queryable file instead
    python get-fred-series.py --with-notes     # include the long descriptions

notes holds each series' prose description and nearly triples the size, so it
is left out unless asked for: about 200 MB of CSV without it against 540 MB
with. Unlike the category tree, this data moves constantly -- FRED touches tens
of thousands of series a day -- so treat any copy of it as a snapshot.

Run `python get-fred-series.py --help` for the available options.
"""

import sqlite3
import sys
import time
from pathlib import Path

from fredapi import (
    FredError, FredMissing, add_client_arguments, build_parser,
    check_client_arguments, client_from_args, elapsed_note, log,
    open_csv_append, progress, read_csv,
)

SERIES_PATH = "fred/category/series"

# Every field fred/category/series returns, notes last so that dropping it
# leaves the column order of the smaller file unchanged.
SERIES_FIELDS = (
    "id", "title", "frequency", "frequency_short", "units", "units_short",
    "seasonal_adjustment", "seasonal_adjustment_short", "observation_start",
    "observation_end", "last_updated", "popularity", "group_popularity",
    "realtime_start", "realtime_end",
)
NOTES_FIELD = "notes"
PAIR_FIELDS = ("series_id", "category_id")


def load_categories(path, root=None):
    """Return the category ids to visit, optionally just one subtree."""
    if not path.exists():
        raise FredError(
            f"no category list at {path}. Run get-fred-id.py first, or point "
            f"--categories at an existing fred-ID-parentID-Names.csv"
        )
    rows = read_csv(path, integer_fields=("id", "parent_id"))
    if root is None:
        return [row["id"] for row in rows]

    children = {}
    for row in rows:
        children.setdefault(row["parent_id"], []).append(row["id"])
    subtree, queue = [], [root]
    while queue:
        current = queue.pop()
        subtree.append(current)
        queue.extend(children.get(current, ()))
    if len(subtree) == 1 and root not in {row["id"] for row in rows}:
        raise FredError(f"category {root} is not in {path.name}")
    return subtree


def read_done(path):
    """Category ids a previous run finished, from the checkpoint file."""
    if not path.exists():
        return set()
    with path.open(encoding="utf-8") as handle:
        return {int(line) for line in handle if line.strip()}


def existing_series_ids(path):
    """Series already written, so a resumed run does not duplicate them."""
    if not path.exists():
        return set()
    return {row["id"] for row in read_csv(path)}


class CsvSink:
    """Writes series and their category pairings to two CSV files.

    Rows go to disk as each category completes rather than being held until
    the end: at 830,000 series the finished file is far too big to buffer.
    """

    def __init__(self, out_dir, fields):
        self.series_path = out_dir / "series.csv"
        self.pairs_path = out_dir / "series-categories.csv"
        self.seen = existing_series_ids(self.series_path)
        if self.seen:
            log(f"  resuming: {len(self.seen):,} series already written")
        self.series_file, self.series_writer = open_csv_append(
            self.series_path, fields, bom=True
        )
        self.pairs_file, self.pairs_writer = open_csv_append(self.pairs_path, PAIR_FIELDS)
        self.pairs = 0

    def add(self, series, category_id):
        """Record a series once, and its pairing with this category always."""
        if series["id"] not in self.seen:
            self.seen.add(series["id"])
            self.series_writer.writerow(series)
        self.pairs_writer.writerow(
            {"series_id": series["id"], "category_id": category_id}
        )
        self.pairs += 1

    def commit(self):
        """Push buffered rows to disk before the category is marked done."""
        self.series_file.flush()
        self.pairs_file.flush()

    def close(self):
        """Note the final count, then release both files."""
        self.total = len(self.seen)
        self.series_file.close()
        self.pairs_file.close()

    def describe(self):
        """Name the output, for the closing summary."""
        return f"{self.series_path.name} and {self.pairs_path.name}"


class SqliteSink:
    """Writes into one SQLite file, letting the database handle deduplication."""

    def __init__(self, out_dir, fields):
        self.path = out_dir / "fred-series.sqlite"
        self.fields = fields
        self.db = sqlite3.connect(self.path)
        columns = ", ".join(
            f"{name} TEXT" + (" PRIMARY KEY" if name == "id" else "") for name in fields
        )
        self.db.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS series ({columns});
            CREATE TABLE IF NOT EXISTS series_categories (
                series_id TEXT NOT NULL,
                category_id INTEGER NOT NULL,
                PRIMARY KEY (series_id, category_id)
            );
            CREATE INDEX IF NOT EXISTS series_categories_category
                ON series_categories (category_id);
            """
        )
        self.db.commit()
        self.pairs = 0

    def add(self, series, category_id):
        """Insert the series and its pairing, ignoring rows already present."""
        placeholders = ", ".join("?" * len(self.fields))
        self.db.execute(
            f"INSERT OR IGNORE INTO series VALUES ({placeholders})",
            [str(series.get(name, "")) for name in self.fields],
        )
        self.db.execute(
            "INSERT OR IGNORE INTO series_categories VALUES (?, ?)",
            (series["id"], category_id),
        )
        self.pairs += 1

    def commit(self):
        """Commit the transaction before the category is marked done."""
        self.db.commit()

    def close(self):
        """Note the final count, then close the database."""
        self.db.commit()
        # Count before closing: the summary is printed after this runs.
        self.total = self.db.execute("SELECT COUNT(*) FROM series").fetchone()[0]
        self.db.close()

    def describe(self):
        """Name the output, for the closing summary."""
        return self.path.name


def crawl(client, sink, categories, checkpoint):
    """Page through every category, checkpointing after each one completes."""
    done = read_done(checkpoint)
    if done:
        log(f"  resuming: {len(done):,} of {len(categories):,} categories already done")
    pending = [c for c in categories if c not in done]
    if not pending:
        log("nothing left to fetch")
        return 0

    visited = 0
    with checkpoint.open("a", encoding="utf-8") as marker:
        for category_id in progress(pending, "series", unit="cat"):
            try:
                rows = client.get_paged(
                    SERIES_PATH, "seriess", {"category_id": category_id},
                    label=f"category {category_id}",
                )
                for series in rows:
                    sink.add(series, category_id)
            except FredMissing:
                log(f"  category {category_id} does not exist, skipping")

            # Only mark the category done once its rows are safely on disk,
            # so a crash re-fetches that one category rather than losing it.
            sink.commit()
            marker.write(f"{category_id}\n")
            marker.flush()
            visited += 1
    return visited


def clear_output(out_dir):
    """Remove previous output so the next run starts from nothing."""
    removed = 0
    for name in ("series.csv", "series-categories.csv", "fred-series.sqlite",
                 ".progress_series.txt"):
        path = out_dir / name
        if path.exists():
            path.unlink()
            removed += 1
    if removed:
        log(f"--refresh: removed {removed} existing file(s) from {out_dir}")


def parse_args(argv):
    """Build the command line, defaulting paths next to this script."""
    here = Path(__file__).resolve().parent
    parser = build_parser("Fetch FRED series metadata for every category.")
    add_client_arguments(parser)
    parser.add_argument(
        "--categories", type=Path, default=here / "fred-ID-parentID-Names.csv",
        help="category list produced by get-fred-id.py",
    )
    parser.add_argument(
        "--out", type=Path, default=here / "saved_series",
        help="directory for the output and its checkpoint",
    )
    parser.add_argument(
        "--root", type=int,
        help="only visit this category and its descendants",
    )
    parser.add_argument(
        "--format", choices=("csv", "sqlite"), default="csv",
        help="two CSV tables, or one SQLite file that deduplicates as it goes",
    )
    parser.add_argument(
        "--with-notes", action="store_true",
        help="include each series' prose description, which quadruples the size",
    )
    args = parser.parse_args(argv)
    check_client_arguments(parser, args)
    return args


def main(argv=None):
    """Run a crawl and return the process exit code."""
    args = parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    if args.refresh:
        clear_output(args.out)

    fields = SERIES_FIELDS + ((NOTES_FIELD,) if args.with_notes else ())
    checkpoint = args.out / ".progress_series.txt"
    client = client_from_args(args)
    started = time.monotonic()
    sink = None

    try:
        categories = load_categories(args.categories, args.root)
        log(f"{len(categories):,} categories to visit from {args.categories.name}")
        sink = CsvSink(args.out, fields) if args.format == "csv" else SqliteSink(
            args.out, fields
        )
        crawl(client, sink, categories, checkpoint)
    except KeyboardInterrupt:
        log("\ninterrupted; progress is checkpointed, rerun to resume")
        return 130
    except FredError as exc:
        log(f"error: {exc}")
        return 1
    finally:
        if sink is not None:
            sink.close()

    log(f"wrote {sink.total:,} series and {sink.pairs:,} category pairings "
        f"to {sink.describe()}")
    log(elapsed_note(started, client.requests_made, "series", sink.total))
    return 0


if __name__ == "__main__":
    sys.exit(main())
