# FRED API ID Fetcher

Two crawlers that reconstruct the catalogue of the [Federal Reserve Economic
Data (FRED) API](https://fred.stlouisfed.org/docs/api/fred/) as CSV: the
category tree, and the metadata of every series filed under it.

FRED used to publish CSV files listing its series and their descriptions and
stopped doing so in 2020. What remains is an API with no endpoint that returns
the category tree, and none that lists the series either — `category/children`
gives you the children of one category, `category/series` the series of one
category, and that is all. Getting the whole picture means walking the API,
which is what these scripts do.

| Script | Fetches | Requests | Time |
| --- | --- | --- | --- |
| `get-fred-id.py` | 5,189 categories: `id`, `name`, `parent_id` | 5,190 | 90 min, or 55 at `--rate 100` |
| `get-fred-series.py` | 845,501 series: title, frequency, units, seasonality, coverage | 5,582 | 85 min |

Those are measured, not estimated. Raising `--rate` speeds the category crawl
up, because its replies are tiny and it really is waiting on the limiter. It
barely helps the series crawl: a page of 1,000 series is most of a megabyte, so
that one spends its time on the wire and settles at about 65 requests a minute
whatever you ask for.

Both crawls resume after an interruption, so neither has to be done in one
sitting.

## Install

```sh
pip install -r requirements.txt
```

Requires Python 3.8+, `requests` and `tqdm`. Get a free API key at
<https://fredaccount.stlouisfed.org/apikeys>, then:

```sh
export FRED_API_KEY=your_key_here      # Windows: set FRED_API_KEY=your_key_here
```

The key is never read from or written to the source. Pass `--api-key` instead
of the environment variable if you prefer.

## Categories

```sh
python get-fred-id.py                  # whole tree, about 90 minutes
python get-fred-id.py --root 33060     # one subtree, about 40 seconds
```

Walks `fred/category/children` breadth first from the root. Cost is lopsided:
a level costs one request per category of the level above it, so about three
quarters of the run goes on the fifth level, which yields 25 categories for
some 3,900 requests.

The tree is currently 9 levels deep and holds **5,189 categories**. Output is
one CSV per depth in `saved_categories/`, plus a combined
`fred-ID-parentID-Names.csv` sorted by `parent_id` then `id`:

```csv
id,name,parent_id
1,Production & Business Activity,0
10,"Population, Employment, & Labor Markets",0
```

This combined file is what the series crawler reads, and what
[FRED-OpenAPI-specification](https://github.com/armanobosyan/FRED-OpenAPI-specification)
publishes as its category listing.

## Series

Run the category crawler first — this one needs its output to know which
categories exist.

```sh
python get-fred-series.py                  # everything, about 90 minutes
python get-fred-series.py --root 32991     # one branch, minutes
python get-fred-series.py --format sqlite  # one queryable file instead of two CSVs
python get-fred-series.py --with-notes     # include the prose descriptions
```

Pages through `fred/category/series` for every category. Series belong to more
than one category, so the output is normalised rather than repeated:
`series.csv` holds each series once, `series-categories.csv` holds the
pairings.

| Output | Rows | Size |
| --- | --- | --- |
| `series.csv` | 845,501 series | 194 MB, or ~540 MB with `--with-notes` |
| `series-categories.csv` | 1,043,049 pairings | 24 MB |
| `fred-series.sqlite` | both tables | ~300 MB |

Series average 1.23 categories each, and 4,903 of the 5,189 categories hold
any series at all; the rest are branches of the tree.

`notes` holds each series' prose description and nearly triples the download,
which is why it is opt-in.

With `--format sqlite` the database deduplicates as it goes and the result is
queryable straight away:

```sql
SELECT id, title, frequency FROM series
  JOIN series_categories ON series_id = id
 WHERE category_id = 125 AND frequency = 'Monthly';
```

Unlike the category tree, which changed by seven rows in two years, series
metadata moves constantly — FRED touches tens of thousands of series a day, and
`last_updated`, `observation_end` and `popularity` change with them. Treat any
copy as a snapshot and refresh it when the freshness matters to you.

## Options

Both scripts share these, though `--root` means "start here" to one and
"only this branch" to the other.

| Option | Default | Purpose |
| --- | --- | --- |
| `--api-key` | `$FRED_API_KEY` | API key |
| `--rate` | `60` | requests per minute (FRED permits 120) |
| `--timeout` | `30` | per-request timeout in seconds |
| `--refresh` | off | delete existing output and checkpoints, start over |
| `--root` | whole tree | category to start from, for a quick trial run |

`get-fred-id.py` adds `--out`, `--combined`, `--no-combined` and `--max-depth`.
`get-fred-series.py` adds `--categories`, `--out`, `--format` and
`--with-notes`. Run either with `--help` for the details.

The exit code is `0` on success, `1` on a failure the run could not recover
from, `2` on a bad command line, and `130` after Ctrl-C.

## Resuming

Neither crawler repeats work it has already done. The category crawler
checkpoints after every request; the series crawler writes its rows to disk and
records the category as it finishes each one, since holding 800,000 series in
memory is not an option.

Interrupt with Ctrl-C and rerun the same command to resume. A resumed run
produces byte-identical output to an uninterrupted one — both crawlers are
tested for that. Use `--refresh` to discard everything and start again.

## Rate limits and errors

FRED permits 120 requests per minute for a registered key and answers with HTTP
429 above that. Both scripts pace themselves to `--rate`, honour `Retry-After`
on 429, and retry connection failures and 5xx responses with exponential
backoff.

Failures are not silently swallowed. A rejected API key aborts the run
immediately; categories FRED reports as nonexistent are logged and skipped. An
empty result is only ever reported when FRED genuinely returns nothing.

## Layout

| File | Purpose |
| --- | --- |
| `get-fred-id.py` | category crawler |
| `get-fred-series.py` | series crawler |
| `fredapi.py` | shared client: pacing, retries, error taxonomy, CSV helpers |

## Background

The Federal Reserve Bank of St. Louis distributed CSV files of series
descriptions until 2020. JD Long wrote a script that reconstructed the
equivalent listing by walking FRED's parent/child category tree, building on
earlier work by Eric Bickel; it depended on the `fredr` package, which has
since been archived on CRAN.

This began as a Python port of that approach without the R dependency, and
adds the pacing, error handling and resume behaviour that crawls of this length
need, plus the series metadata the original CSVs carried.

## License

See [LICENSE](LICENSE).
