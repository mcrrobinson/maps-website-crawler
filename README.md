# maps-website-crawler

Crawls businesses outward from a starting location using the official
**Google Places API (New)**, collecting name, address, and website for each
place it finds. Search starts at a single point and expands ring-by-ring in
a grid of overlapping tiles until a radius/result cap is hit or a ring of
tiles turns up nothing new.

Results are stored in a local SQLite database (deduplicated by place ID, so
re-running a crawl is cheap — already-searched tiles are skipped) and can be
exported to CSV.

## Setup

1. **Get an API key** (Google Cloud Console):
   - Create/select a project.
   - Enable **Places API (New)** and **Geocoding API**.
   - If you also want to run Lighthouse audits (see below), enable
     **PageSpeed Insights API** on the same project — it's free and, unlike
     Places API (New), doesn't require billing to be enabled.
   - Create an API key under *APIs & Services > Credentials*.
   - Billing must be enabled on the project for the Places/Geocoding APIs —
     Google's free tier covers a meaningful amount of usage, but this app
     can burn through it quickly on large crawls (see **Cost** below).

2. **Install dependencies:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Configure your API key and (optionally) a default starting location:**
   ```bash
   cp .env.example .env
   ```
   Then edit `.env`:
   ```
   GOOGLE_MAPS_API_KEY=your-key-here
   ADDRESS=1600 Amphitheatre Parkway, Mountain View, CA
   # ...or instead of ADDRESS, set LAT and LNG
   ```
   `.env` is loaded automatically and is gitignored, so it's safe to put a
   real key in it. Values in `.env` are just defaults — passing `--address`,
   `--lat`/`--lng`, or `--api-key` on the command line always overrides them.
   (You can also skip `.env` entirely and use `export GOOGLE_MAPS_API_KEY=...`
   instead.)

## Usage

With `GOOGLE_MAPS_API_KEY` and `ADDRESS` (or `LAT`/`LNG`) set in `.env`, you can
just run:
```bash
python -m maps_crawler --tile-radius 500 --max-rings 5 --csv results.csv
```

Or override the location per run without touching `.env`:

Start from an address:
```bash
python -m maps_crawler --address "1600 Amphitheatre Parkway, Mountain View, CA" \
  --tile-radius 500 --max-rings 5 --csv results.csv
```

Start from your current coordinates (get these from your phone's GPS, or any
"what's my location" tool — this app does not auto-detect it):
```bash
python -m maps_crawler --lat 37.4220 --lng -122.0841 \
  --tile-radius 500 --max-rings 5 --db mountain_view.db --csv results.csv
```

Only export businesses that actually have a website on file:
```bash
python -m maps_crawler --lat 37.4220 --lng -122.0841 \
  --csv with_sites.csv --with-website-only
```

Restrict to certain business types ([full type list](https://developers.google.com/maps/documentation/places/web-service/place-types)):
```bash
python -m maps_crawler --address "Austin, TX" --types restaurant,cafe,bar
```

### Key options

| Flag | Default | Meaning |
|---|---|---|
| `--tile-radius` | 700 | Search radius per tile, in meters |
| `--max-rings` | 6 | How many rings to expand outward (ring 0 = origin tile) |
| `--early-stop-empty-rings` | 2 | Stop early after N consecutive rings with zero *new* places |
| `--max-places` | none | Hard cap on total places collected |
| `--qps` | 8 | Max requests/second to the Places API |
| `--types` | all | Comma-separated included place types |
| `--force` | off | Re-search tiles already marked visited in the DB |
| `--db` | `places.db` | SQLite database path |
| `--csv` | none | Export path; only written if set |
| `--with-website-only` | off | Filter CSV export to places with a website |

Re-running the same command with the same `--db` resumes/extends the crawl
without re-paying for tiles already searched (bump `--max-rings` to grow the
covered area).

## Cost

Each tile is one Nearby Search (New) call. A crawl with `--max-rings 6`
covers roughly `1 + 8 + 16 + 24 + 32 + 40 + 48 = 169` tiles. Nearby Search
(New) is billed per request under Google's Places API pricing — check the
[current pricing](https://mapsplatform.google.com/pricing/) and your
project's quota before running large crawls. Use `--max-places` or a smaller
`--max-rings` to bound spend on a first run.

## Lighthouse audits

Once you have a `results.csv` (or any CSV with a `website` column), you can
run every site through Google's hosted Lighthouse — the **PageSpeed
Insights API** — to get performance/accessibility/best-practices/SEO
scores, core web vitals, and a full-page screenshot for each.

This runs entirely against Google's infrastructure (no local Chrome
needed), which matters at this scale: hundreds of arbitrary small-business
sites include plenty that are slow, broken, or misconfigured, and letting
Google's servers absorb that instead of a local headless Chrome instance is
far more reliable than running Lighthouse locally.

```bash
python -m maps_crawler.lighthouse_cli --input-csv results.csv
```

This audits every unique website in `results.csv` on **both mobile and
desktop** (911 sites -> ~1,822 audits), storing results in `lighthouse.db`,
exporting a summary to `lighthouse_results.csv`, and saving one full-page
screenshot per (site, strategy) under `screenshots/` — handy for feeding
into an LLM afterward for visual review.

Progress is checkpointed as it runs (each result is committed to
`lighthouse.db` immediately), and re-running the same command skips sites
that already have a successful audit — so an interrupted run can just be
resumed.

### Key options

| Flag | Default | Meaning |
|---|---|---|
| `--input-csv` | `results.csv` | CSV with a website column to audit |
| `--website-column` | `website` | Column name holding the URL |
| `--strategy` | `both` | `mobile`, `desktop`, or `both` |
| `--qps` | 3 | Max PageSpeed Insights requests/second (stay under your project's quota) |
| `--max-workers` | 8 | Concurrent in-flight requests (audits are latency-bound, so this matters more than `--qps` for wall-clock time — but too many *simultaneous* in-flight requests can trip PSI's abuse guard, so raise gradually) |
| `--force` | off | Re-audit sites that already have a successful result |
| `--db` | `lighthouse.db` | SQLite database path |
| `--out-csv` | `lighthouse_results.csv` | CSV export path |
| `--screenshot-dir` | `screenshots` | Where full-page screenshots are saved |

Each row in the exported CSV has: `website`, `strategy`, `final_url`,
`performance_score`, `accessibility_score`, `best_practices_score`,
`seo_score`, `first_contentful_paint_ms`, `largest_contentful_paint_ms`,
`speed_index_ms`, `total_blocking_time_ms`, `cumulative_layout_shift`,
`time_to_interactive_ms`, `screenshot_path`, `error`, `audited_at`.

PageSpeed Insights' default quota is 25,000 queries/day and ~400
queries/100s per project — the default `--qps 3` stays comfortably under
that. If you see 429s, lower `--qps`; if audits feel slow, raise
`--max-workers` (each individual audit takes Google ~15-30s to run, so
concurrency — not request rate — is what determines wall-clock time).

## Project layout

```
maps_crawler/
  cli.py                argument parsing / entrypoint for the crawler
  crawler.py            ring-by-ring crawl orchestration
  grid.py                expanding-tile grid math
  places_client.py      Places API (New) HTTP client (retries, QPS throttling)
  geocode.py              address -> lat/lng via Geocoding API
  storage.py              SQLite persistence + CSV export for places
  lighthouse_cli.py      argument parsing / entrypoint for Lighthouse audits
  lighthouse.py            concurrent audit orchestration
  lighthouse_client.py    PageSpeed Insights API HTTP client + response parsing
  lighthouse_storage.py   SQLite persistence + CSV export for audits
tests/
  test_grid.py             unit tests for the grid math
```

## Tests

```bash
pytest
```
