# FRED API ID Fetcher

Downloads the complete category tree of the [Federal Reserve Economic Data
(FRED) API](https://fred.stlouisfed.org/docs/api/fred/) and saves it as CSV.

FRED organises its ~800,000 time series under a tree of categories, but offers
no endpoint that returns the tree in one call — only
`fred/category/children`, which lists the direct children of a single
category. This script walks that endpoint breadth first until the whole tree
has been visited: about **5,200 requests**, roughly **90 minutes** at the
default pace of 60 requests per minute, or **45** at `--rate 120`, the most
FRED allows.

The result is the `id`, `name` and `parent_id` of every FRED category, which is
what you need to:

- resolve a category id from an API response to a readable name without a
  round trip, which matters when you are processing thousands of rows;
- find the category id to pass to `fred/category/series`, instead of walking
  down from the root with one request per level every time you search;
- build a browsable tree, autocomplete or facet filter over FRED, none of
  which can assemble the hierarchy live at 120 requests per minute;
- hand an LLM agent the whole list up front rather than have it spend its
  context discovering the tree.

Note that this is category metadata, not data: no series, no observations.

The generated file is what
[FRED-OpenAPI-specification](https://github.com/armanobosyan/FRED-OpenAPI-specification)
publishes as its category listing.

## Install

```sh
pip install -r requirements.txt
```

Requires Python 3.8+, `requests` and `tqdm`.

## Usage

Get a free API key at <https://fredaccount.stlouisfed.org/apikeys>, then:

```sh
export FRED_API_KEY=your_key_here      # Windows: set FRED_API_KEY=your_key_here
python get-fred-id.py
```

The key is never read from or written to the source. Pass `--api-key` instead
of the environment variable if you prefer.

To try it out without waiting for the full tree, crawl a single subtree:

```sh
python get-fred-id.py --root 33060     # "Academic Data", 65 categories, ~40s
```

Progress is reported per level, with a bar showing the categories being
expanded:

```
level 0: expanding 1 categories
level 1: expanding 8 categories
level 2: expanding 73 categories
level 3: expanding 632 categories
...
wrote 5189 categories to fred-ID-parentID-Names.csv
done: 5189 categories, 5190 requests, 53.9 min
```

The exit code is `0` on success, `1` on a failure the run could not recover
from, `2` on a bad command line, and `130` after Ctrl-C.

### Options

| Option | Default | Purpose |
| --- | --- | --- |
| `--api-key` | `$FRED_API_KEY` | API key |
| `--out` | `saved_categories/` | directory for the per-level CSVs |
| `--combined` | `fred-ID-parentID-Names.csv` | combined CSV of every category |
| `--no-combined` | off | skip the combined CSV |
| `--root` | `0` | category to start from |
| `--rate` | `60` | requests per minute (FRED permits 120) |
| `--max-depth` | `12` | stop after this many levels |
| `--timeout` | `30` | per-request timeout in seconds |
| `--refresh` | off | delete existing CSVs and checkpoints, start over |

## Output

`saved_categories/fetched_level_N.csv` holds the categories found at depth `N`,
and `fred-ID-parentID-Names.csv` holds all of them, sorted by `parent_id` then
`id`. Both use the columns `id,name,parent_id`.

```csv
id,name,parent_id
32991,"Money, Banking, & Finance",0
10,"Population, Employment, & Labor Markets",0
```

The tree is currently 9 levels deep and holds about 5,200 categories. The
combined file is written with a UTF-8 BOM so that Excel reads names such as
`Côte d'Ivoire` correctly rather than corrupting them on save.

## Resuming

Every request is checkpointed to `saved_categories/.progress_level_N.jsonl`
as it completes, so an interrupted run continues where it stopped rather than
repeating the level. This matters: the fifth level alone costs about 3,900
requests, since a level costs one request per category of the level above it.

Interrupt with Ctrl-C and rerun the same command to resume. Levels already
written as CSV are reused as-is; use `--refresh` to discard everything and
crawl from scratch. Checkpoint files are removed automatically once their
level completes, and a resumed run produces byte-identical output to an
uninterrupted one.

## Rate limits and errors

FRED permits 120 requests per minute for a registered key and answers with
HTTP 429 above that. The script paces itself to `--rate` requests per minute,
honours `Retry-After` on 429, and retries connection failures and 5xx
responses with exponential backoff.

Failures are not silently swallowed: a rejected API key aborts the run
immediately, and categories FRED reports as nonexistent are logged as skipped.
An empty result is only ever reported when FRED genuinely returns no children.

## Background

The Federal Reserve Bank of St. Louis distributed CSV files of series
descriptions until May 2020, when it stopped. JD Long wrote a script that
reconstructed the equivalent listing by walking FRED's parent/child category
tree, building on earlier work by Eric Bickel; it depended on the `fredr`
package, which has since been archived on CRAN.

This is a Python port of that approach, without the R dependency, and adds the
rate limiting and resume behaviour that a crawl of this length needs.

## License

See [LICENSE](LICENSE).
