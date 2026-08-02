#!/usr/bin/env python3
"""Shared plumbing for the FRED crawlers in this repository.

Holds the pieces both get-fred-id.py and get-fred-series.py need: a paced,
retrying HTTP client, the error taxonomy, progress reporting and CSV helpers.
Nothing here is FRED-endpoint specific beyond the base URL.
"""

import argparse
import csv
import os
import sys
import time

import requests

API_ROOT = "https://api.stlouisfed.org"

# FRED allows 120 requests/minute for a registered key. The default leaves
# generous headroom; the long crawls in this repository run for an hour either
# way, and a user is likely to have other things pointed at the same key.
DEFAULT_RATE = 60
MAX_RATE = 120

# The largest page fred/* endpoints will return.
MAX_PAGE = 1000

try:
    from tqdm import tqdm
except ImportError:  # progress bars are a nicety, not a requirement
    tqdm = None


class FredError(RuntimeError):
    """A condition the caller cannot recover from (bad key, refused host)."""


class FredMissing(FredError):
    """FRED reports the requested object does not exist; usually skippable."""


def log(message):
    """Print without corrupting an active tqdm bar."""
    if tqdm is not None:
        tqdm.write(message, file=sys.stderr)
    else:
        print(message, file=sys.stderr, flush=True)


def progress(iterable, description, unit="item", total=None):
    """Wrap an iterable in a progress bar when tqdm is installed."""
    if tqdm is None:
        return iterable
    return tqdm(iterable, desc=description, unit=unit, total=total, leave=False)


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
    """Paced, retrying JSON client for the FRED API.

    Failures are separated rather than flattened into an empty result: a
    rejected key, a missing object, throttling and a server fault each get
    their own treatment, because conflating them is what made the previous
    version of this crawler report success while fetching nothing.
    """

    def __init__(self, api_key, rate=DEFAULT_RATE, timeout=30, max_attempts=5):
        self.api_key = api_key
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.limiter = RateLimiter(rate)
        self.session = requests.Session()
        self.requests_made = 0

    def get(self, path, params=None, label=None):
        """GET one FRED endpoint and return the decoded JSON body.

        `label` names the thing being fetched in error messages. Raises
        FredMissing if FRED says the object does not exist, FredError for
        anything else that cannot be retried away.
        """
        label = label or path
        query = dict(params or {})
        query.update(file_type="json", api_key=self.api_key)

        for attempt in range(1, self.max_attempts + 1):
            self.limiter.wait()
            try:
                response = self.session.get(
                    f"{API_ROOT}/{path.lstrip('/')}", params=query, timeout=self.timeout
                )
            except requests.RequestException as exc:
                self._backoff(attempt, f"{label}: {exc}")
                continue

            self.requests_made += 1

            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError as exc:
                    self._backoff(attempt, f"{label}: malformed JSON ({exc})")
                    continue

            if response.status_code == 429:
                pause = float(response.headers.get("Retry-After", 10))
                log(f"  rate limited, waiting {pause:.0f}s")
                time.sleep(pause)
                continue

            if response.status_code >= 500:
                detail = self._error_message(response)
                # FRED answers 500 for some malformed requests, e.g. a
                # series/updates window older than it will serve.
                if "must come within" in detail:
                    raise FredError(f"{label}: {detail}")
                self._backoff(attempt, f"{label}: HTTP {response.status_code}")
                continue

            detail = self._error_message(response)
            if "api_key" in detail:
                raise FredError(f"FRED rejected the API key: {detail}")
            if "does not exist" in detail:
                raise FredMissing(f"{label}: {detail}")
            raise FredError(f"{label}: HTTP {response.status_code} {detail}")

        raise FredError(f"{label}: giving up after {self.max_attempts} attempts")

    def get_paged(self, path, envelope, params=None, label=None, page_size=MAX_PAGE):
        """Yield every row of a paged endpoint, following count and offset."""
        offset = 0
        while True:
            page = dict(params or {}, limit=page_size, offset=offset)
            body = self.get(path, page, label=label)
            rows = body.get(envelope, [])
            if not rows:
                return
            for row in rows:
                yield row
            offset += len(rows)
            if offset >= body.get("count", offset):
                return

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


def add_client_arguments(parser):
    """Add the options every crawler here shares."""
    parser.add_argument(
        "--api-key",
        default=os.environ.get("FRED_API_KEY"),
        help="FRED API key; defaults to the FRED_API_KEY environment variable",
    )
    parser.add_argument(
        "--rate", type=int, default=DEFAULT_RATE,
        help=f"requests per minute (FRED permits up to {MAX_RATE})",
    )
    parser.add_argument("--timeout", type=float, default=30, help="per-request timeout")
    parser.add_argument(
        "--refresh", action="store_true",
        help="delete existing output and checkpoints before starting",
    )
    return parser


def check_client_arguments(parser, args):
    """Reject a command line that cannot produce a working client."""
    if not args.api_key:
        parser.error(
            "no API key: set FRED_API_KEY or pass --api-key "
            "(get one at https://fredaccount.stlouisfed.org/apikeys)"
        )
    if not 1 <= args.rate <= MAX_RATE:
        parser.error(f"--rate must be between 1 and {MAX_RATE}")


def client_from_args(args):
    """Build a client from the shared options."""
    return FredClient(args.api_key, rate=args.rate, timeout=args.timeout)


def write_csv(path, rows, fieldnames, bom=False):
    """Write rows as CSV. Set bom for a file Excel will open without mangling."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig" if bom else "utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def open_csv_append(path, fieldnames, bom=False):
    """Open a CSV for appending, writing the header if the file is new.

    Returns the file handle and a DictWriter. Used by crawls whose output is
    too large to hold in memory until the end.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not path.exists() or path.stat().st_size == 0
    # Deliberately not the utf-8-sig codec: it emits the BOM on the first write
    # to the handle, which on an append would plant one in the middle of the
    # file. Write it by hand instead, and only for a new file.
    handle = path.open("a", newline="", encoding="utf-8")
    writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
    if fresh:
        if bom:
            handle.write("﻿")
        writer.writeheader()
        handle.flush()
    return handle, writer


def read_csv(path, integer_fields=()):
    """Read a CSV back, converting the named columns to int."""
    with path.open(encoding="utf-8-sig") as handle:
        rows = []
        for row in csv.DictReader(handle):
            for field in integer_fields:
                row[field] = int(row[field])
            rows.append(row)
        return rows


def elapsed_note(started, requests_made, subject, count):
    """Format the one-line summary every crawler prints when it finishes."""
    minutes = (time.monotonic() - started) / 60
    return f"done: {count:,} {subject}, {requests_made:,} requests, {minutes:.1f} min"


def build_parser(description):
    """Start a parser with the conventions used across these scripts."""
    return argparse.ArgumentParser(
        description=description,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
