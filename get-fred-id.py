#!/usr/bin/env python3
"""Fetch the complete FRED category tree and save it as CSV.

FRED offers no endpoint that returns its category tree, only
fred/category/children, which lists the direct children of one category. This
script walks that endpoint breadth first from the root until every branch ends,
which currently takes about 5,200 requests and finds about 5,200 categories.

Cost is lopsided: a level costs one request per category of the level above it,
so roughly three quarters of the run is spent on the fifth level, which yields
25 categories for about 3,900 requests. That is why progress is checkpointed
after every single request rather than per level -- an interrupted run resumes
where it stopped, and produces byte-identical output to an uninterrupted one.

Writes saved_categories/fetched_level_N.csv per depth, and a combined
fred-ID-parentID-Names.csv sorted by parent_id then id. Both carry the columns
id, name and parent_id. The combined file is what get-fred-series.py reads.

    export FRED_API_KEY=...          # https://fredaccount.stlouisfed.org/apikeys
    python get-fred-id.py            # whole tree, about 90 minutes
    python get-fred-id.py --root 33060   # one subtree, about 40 seconds

Run `python get-fred-id.py --help` for the available options.
"""

import json
import sys
import time
from pathlib import Path

from fredapi import (
    FredError, FredMissing, add_client_arguments, build_parser,
    check_client_arguments, client_from_args, elapsed_note, log, progress,
    read_csv, write_csv,
)

CHILDREN_PATH = "fred/category/children"
ROOT_CATEGORY = 0
FIELDNAMES = ("id", "name", "parent_id")

# The tree currently bottoms out at depth 8. The cap only exists so a cycle or
# an unexpectedly deep branch cannot spin forever; hitting it is reported.
DEFAULT_MAX_DEPTH = 12


def fetch_children(client, category_id):
    """Return the child categories of `category_id`; empty for a leaf."""
    try:
        body = client.get(
            CHILDREN_PATH, {"category_id": category_id}, label=f"category {category_id}"
        )
    except FredMissing:
        log(f"  category {category_id} does not exist, skipping")
        return []
    return body.get("categories", [])


def read_checkpoint(path):
    """Return {parent_id: [child, ...]} recorded by an interrupted run."""
    done = {}
    if not path.exists():
        return done
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                # A partial final line means the process died mid-write.
                log(f"  discarding truncated checkpoint entry in {path.name}")
                continue
            done[entry["parent"]] = entry["children"]
    return done


def fetch_level(client, parents, checkpoint_path, description):
    """Fetch the children of every parent, checkpointing after each request."""
    done = read_checkpoint(checkpoint_path)
    if done:
        log(f"  resuming: {len(done)} of {len(parents)} already fetched")

    pending = [p for p in parents if p not in done]
    if pending:
        with checkpoint_path.open("a", encoding="utf-8") as handle:
            for parent in progress(pending, description, unit="cat"):
                children = fetch_children(client, parent)
                done[parent] = children
                handle.write(json.dumps({"parent": parent, "children": children}) + "\n")
                handle.flush()

    # Keep the order parents were queued in, so reruns produce identical files.
    return [child for parent in parents for child in done.get(parent, [])]


def crawl(client, out_dir, root=ROOT_CATEGORY, max_depth=DEFAULT_MAX_DEPTH):
    """Walk the tree level by level. Returns every category found."""
    out_dir.mkdir(parents=True, exist_ok=True)
    everything = []
    seen = {root}
    queue = [root]

    for level in range(max_depth):
        if not queue:
            log(f"level {level}: nothing left to expand, tree is complete")
            break

        level_csv = out_dir / f"fetched_level_{level}.csv"
        checkpoint = out_dir / f".progress_level_{level}.jsonl"
        if level_csv.exists():
            rows = read_csv(level_csv, integer_fields=("id", "parent_id"))
            log(f"level {level}: {len(rows)} categories loaded from {level_csv.name}")
        else:
            log(f"level {level}: expanding {len(queue)} categories")
            rows = fetch_level(client, queue, checkpoint, f"level {level}")
            checkpoint.unlink(missing_ok=True)
            if rows:
                write_csv(level_csv, rows, FIELDNAMES)
                log(f"level {level}: found {len(rows)} categories -> {level_csv.name}")
            else:
                log(f"level {level}: no children, the tree ends here")

        if not rows:
            break

        everything.extend(rows)
        queue = []
        for row in rows:
            if row["id"] in seen:
                # FRED's tree has no cross-links today; this guards against a
                # cycle turning the crawl into an infinite loop.
                log(f"  category {row['id']} already visited, not expanding again")
                continue
            seen.add(row["id"])
            queue.append(row["id"])
    else:
        if queue:
            log(
                f"WARNING: stopped at the --max-depth limit of {max_depth} with "
                f"{len(queue)} categories still unexpanded; rerun with a larger limit"
            )

    return everything


def clear_output(out_dir):
    """Delete the level CSVs and checkpoints so the next crawl starts clean."""
    removed = 0
    for pattern in ("fetched_level_*.csv", ".progress_level_*.jsonl"):
        for path in out_dir.glob(pattern):
            path.unlink()
            removed += 1
    if removed:
        log(f"--refresh: removed {removed} existing file(s) from {out_dir}")


def parse_args(argv):
    """Build the command line, defaulting paths next to this script."""
    here = Path(__file__).resolve().parent
    parser = build_parser("Fetch the FRED category tree into CSV files.")
    add_client_arguments(parser)
    parser.add_argument(
        "--out", type=Path, default=here / "saved_categories",
        help="directory for the per-level CSV files",
    )
    parser.add_argument(
        "--combined", type=Path, default=here / "fred-ID-parentID-Names.csv",
        help="path of the combined CSV of every category",
    )
    parser.add_argument(
        "--no-combined", action="store_true", help="skip writing the combined CSV"
    )
    parser.add_argument(
        "--root", type=int, default=ROOT_CATEGORY,
        help="category to start from; use a subtree id for a quick trial run",
    )
    parser.add_argument(
        "--max-depth", type=int, default=DEFAULT_MAX_DEPTH,
        help="stop after this many levels",
    )
    args = parser.parse_args(argv)
    check_client_arguments(parser, args)
    return args


def main(argv=None):
    """Run a crawl and return the process exit code."""
    args = parse_args(argv)

    if args.refresh and args.out.exists():
        clear_output(args.out)

    client = client_from_args(args)
    started = time.monotonic()
    try:
        categories = crawl(client, args.out, root=args.root, max_depth=args.max_depth)
    except KeyboardInterrupt:
        log("\ninterrupted; progress is checkpointed, rerun to resume")
        return 130
    except FredError as exc:
        log(f"error: {exc}")
        return 1

    if not categories:
        log("error: no categories were returned")
        return 1

    if not args.no_combined:
        # Sorted by parent then id, matching fred-ID-parentID-Names.csv in the
        # FRED-OpenAPI-specification repository. The BOM keeps Excel from
        # mangling non-ASCII names such as "Côte d'Ivoire".
        combined = sorted(categories, key=lambda r: (r["parent_id"], r["id"]))
        write_csv(args.combined, combined, FIELDNAMES, bom=True)
        log(f"wrote {len(combined):,} categories to {args.combined}")

    log(elapsed_note(started, client.requests_made, "categories", len(categories)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
