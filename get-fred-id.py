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
id, name and parent_id.

    export FRED_API_KEY=...          # https://fredaccount.stlouisfed.org/apikeys
    python get-fred-id.py            # whole tree, about 90 minutes
    python get-fred-id.py --root 33060   # one subtree, about 40 seconds

Run `python get-fred-id.py --help` for the available options.
"""

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

import requests

CHILDREN_URL = "https://api.stlouisfed.org/fred/category/children"
ROOT_CATEGORY = 0
FIELDNAMES = ("id", "name", "parent_id")

# FRED allows 120 requests/minute for a registered key. The default leaves
# generous headroom; a full crawl is ~5200 requests either way.
DEFAULT_RATE = 60
MAX_RATE = 120

# The tree currently bottoms out at depth 8. The cap only exists so a cycle or
# an unexpectedly deep branch cannot spin forever; hitting it is reported.
DEFAULT_MAX_DEPTH = 12

try:
    from tqdm import tqdm
except ImportError:  # progress bars are a nicety, not a requirement
    tqdm = None


class FredError(RuntimeError):
    """A condition the crawl cannot recover from (bad key, refused host)."""


def log(message):
    """Print without corrupting an active tqdm bar."""
    if tqdm is not None:
        tqdm.write(message, file=sys.stderr)
    else:
        print(message, file=sys.stderr, flush=True)


class RateLimiter:
    """Paces calls to a target rate, counting time already spent in-flight."""

    def __init__(self, per_minute):
        self.interval = 60.0 / per_minute
        self.next_slot = 0.0

    def wait(self):
        """Block until the next request is due, then claim that slot."""
        delay = self.next_slot - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self.next_slot = time.monotonic() + self.interval


class FredClient:
    """Thin client for fred/category/children with retries and pacing."""

    def __init__(self, api_key, rate=DEFAULT_RATE, timeout=30, max_attempts=5):
        self.api_key = api_key
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.limiter = RateLimiter(rate)
        self.session = requests.Session()
        self.requests_made = 0

    def children(self, category_id):
        """Return the child categories of `category_id` as a list of dicts.

        An empty list means the category is a leaf. Categories that FRED
        reports as nonexistent are skipped with a warning; anything that
        suggests the crawl itself is broken raises FredError.
        """
        params = {
            "category_id": category_id,
            "file_type": "json",
            "api_key": self.api_key,
        }
        for attempt in range(1, self.max_attempts + 1):
            self.limiter.wait()
            try:
                response = self.session.get(
                    CHILDREN_URL, params=params, timeout=self.timeout
                )
            except requests.RequestException as exc:
                self._backoff(attempt, f"category {category_id}: {exc}")
                continue

            self.requests_made += 1

            if response.status_code == 200:
                return response.json().get("categories", [])

            if response.status_code == 429:
                pause = float(response.headers.get("Retry-After", 10))
                log(f"  rate limited, waiting {pause:.0f}s")
                time.sleep(pause)
                continue

            if response.status_code >= 500:
                self._backoff(attempt, f"category {category_id}: HTTP {response.status_code}")
                continue

            # 4xx other than 429: FRED explains itself in the body.
            detail = self._error_message(response)
            if "api_key" in detail:
                raise FredError(f"FRED rejected the API key: {detail}")
            if "does not exist" in detail:
                log(f"  category {category_id} does not exist, skipping")
                return []
            raise FredError(f"category {category_id}: HTTP {response.status_code} {detail}")

        raise FredError(
            f"category {category_id}: giving up after {self.max_attempts} attempts"
        )

    def _backoff(self, attempt, reason):
        """Sleep before the next attempt, or give up once they are exhausted."""
        if attempt >= self.max_attempts:
            raise FredError(f"{reason} (final attempt)")
        pause = 2 ** attempt
        log(f"  {reason}; retrying in {pause}s")
        time.sleep(pause)

    @staticmethod
    def _error_message(response):
        """Pull FRED's explanation out of an error body, however it is shaped."""
        try:
            return str(response.json().get("error_message", response.text[:200]))
        except ValueError:
            return response.text[:200]


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
            bar = tqdm(pending, desc=description, unit="cat", leave=False) if tqdm else pending
            for parent in bar:
                children = client.children(parent)
                done[parent] = children
                handle.write(json.dumps({"parent": parent, "children": children}) + "\n")
                handle.flush()

    # Keep the order parents were queued in, so reruns produce identical files.
    return [child for parent in parents for child in done.get(parent, [])]


def write_csv(path, rows, bom=False):
    """Write category rows as CSV. Set bom for a file Excel will open safely."""
    encoding = "utf-8-sig" if bom else "utf-8"
    with path.open("w", newline="", encoding=encoding) as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path):
    """Read back a level CSV written by an earlier run, ids as integers."""
    with path.open(encoding="utf-8-sig") as handle:
        return [
            {"id": int(r["id"]), "name": r["name"], "parent_id": int(r["parent_id"])}
            for r in csv.DictReader(handle)
        ]


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
        if level_csv.exists():
            rows = read_csv(level_csv)
            log(f"level {level}: {len(rows)} categories loaded from {level_csv.name}")
        else:
            log(f"level {level}: expanding {len(queue)} categories")
            rows = fetch_level(
                client,
                queue,
                out_dir / f".progress_level_{level}.jsonl",
                f"level {level}",
            )
            (out_dir / f".progress_level_{level}.jsonl").unlink(missing_ok=True)
            if rows:
                write_csv(level_csv, rows)
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
    parser = argparse.ArgumentParser(
        description="Fetch the FRED category tree into CSV files.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("FRED_API_KEY"),
        help="FRED API key; defaults to the FRED_API_KEY environment variable",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=here / "saved_categories",
        help="directory for the per-level CSV files",
    )
    parser.add_argument(
        "--combined",
        type=Path,
        default=here / "fred-ID-parentID-Names.csv",
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
        "--rate", type=int, default=DEFAULT_RATE,
        help=f"requests per minute (FRED permits up to {MAX_RATE})",
    )
    parser.add_argument(
        "--max-depth", type=int, default=DEFAULT_MAX_DEPTH,
        help="stop after this many levels",
    )
    parser.add_argument("--timeout", type=float, default=30, help="per-request timeout")
    parser.add_argument(
        "--refresh", action="store_true",
        help="delete existing level CSVs and checkpoints before starting",
    )
    args = parser.parse_args(argv)

    if not args.api_key:
        parser.error(
            "no API key: set FRED_API_KEY or pass --api-key "
            "(get one at https://fredaccount.stlouisfed.org/apikeys)"
        )
    if not 1 <= args.rate <= MAX_RATE:
        parser.error(f"--rate must be between 1 and {MAX_RATE}")
    return args


def main(argv=None):
    """Run a crawl and return the process exit code."""
    args = parse_args(argv)

    if args.refresh and args.out.exists():
        clear_output(args.out)

    client = FredClient(args.api_key, rate=args.rate, timeout=args.timeout)
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
        write_csv(args.combined, combined, bom=True)
        log(f"wrote {len(combined)} categories to {args.combined}")

    elapsed = time.monotonic() - started
    log(
        f"done: {len(categories)} categories, {client.requests_made} requests, "
        f"{elapsed / 60:.1f} min"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
