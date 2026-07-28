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
   - Create an API key under *APIs & Services > Credentials*.
   - Billing must be enabled on the project for the Places/Geocoding APIs —
     Google's free tier covers a meaningful amount of usage, but this app
     can burn through it quickly on large crawls (see **Cost** below).
   - If you also want to run website quality audits (see below), get an
     **Anthropic API key** from [console.anthropic.com](https://console.anthropic.com/)
     — this is a separate service from Google's.

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

## Website quality audits

Once you have a `results.csv` (or any CSV with a `website` column), you can
run every site through a local browser-based audit that judges the three
things that actually matter for a small-business website, in priority
order:

1. **Does it look good** — Claude (vision) scores a full-page screenshot
   of the homepage against a design rubric (layout, typography, clutter,
   how dated it looks) and writes a short critique, separately for
   desktop and mobile.
2. **Are its links valid** — the homepage plus up to `--max-pages-per-site`
   internal pages are crawled, every discovered link (internal and
   external) is checked, and broken ones (4xx/5xx/timeouts/DNS failures)
   are flagged.
3. **How fast does it load** — real navigation timing captured from the
   browser itself.

This replaced an earlier version built on Google's hosted Lighthouse
(PageSpeed Insights) API, which only ever scored a single URL and had no
way to verify a site's own links or crawl past the homepage. Getting a
clean screenshot also requires clearing cookie-consent banners — this
pipeline runs a real headless Chromium (via Playwright) locally, using
[DuckDuckGo's `autoconsent`](https://github.com/duckduckgo/autoconsent)
library (vendored under `vendor/autoconsent/`, the same engine that
powers cookie handling in Firefox/Brave) plus a text-matching fallback to
clear consent banners — including ones rendered in a cross-origin CMP
iframe — before every screenshot.

```bash
python -m maps_crawler.site_audit_cli --input-csv results.csv
```

Progress is checkpointed as it runs (each result is committed to
`site_audit.db` immediately), and re-running the same command skips sites
that already have a successful audit — so an interrupted run can just be
resumed.

### Key options

| Flag | Default | Meaning |
|---|---|---|
| `--input-csv` | `results.csv` | CSV with a website column to audit |
| `--website-column` | `website` | Column name holding the URL |
| `--max-pages-per-site` | 15 | Max pages crawled per site for link discovery (homepage included) |
| `--max-concurrency` | 4 | Max sites crawled concurrently — real browser tabs are heavier than HTTP calls, so this stays low by default |
| `--vision-model` | `claude-sonnet-5` | Anthropic model used for look-scoring |
| `--force` | off | Re-audit sites that already have a successful result |
| `--db` | `site_audit.db` | SQLite database path |
| `--out-csv` | `site_audit_results.csv` | Per-site CSV export path |
| `--links-csv` | `site_audit_links.csv` | Per-link CSV export path |
| `--screenshot-dir` | `screenshots` | Where desktop/mobile screenshots are saved |

Each row in `site_audit_results.csv` has: `website`, `final_url`,
`http_status`, `pages_crawled`, `load_time_ms`, `desktop_look_score`,
`desktop_look_summary`, `desktop_issues`, `mobile_look_score`,
`mobile_look_summary`, `mobile_issues`, `desktop_screenshot_path`,
`mobile_screenshot_path`, `broken_link_count`, `total_link_count`,
`error`, `audited_at`. `site_audit_links.csv` has one row per checked
link: `website`, `source_page`, `link_url`, `status_code`,
`classification` (`ok`/`redirect`/`broken`/`error`), `error`,
`checked_at`.

Two cost/runtime notes:
- **Runtime**: crawling with a real local browser is much slower per site
  than a single hosted PageSpeed Insights call was. `--max-concurrency`
  and `--max-pages-per-site` are the levers to bound total wall-clock time
  across a large `results.csv`.
- **Vision API cost**: each site makes 2 Claude vision calls (desktop +
  mobile look-scoring), plus occasional extra calls when the automatic
  consent-banner clearing needs a vision-guided fallback — same
  "watch your spend at scale" caveat the Cost section above gives for the
  Places API.
- **False positives on link checks**: some sites block automated HEAD/GET
  requests (e.g. behind bot protection) even though the link works fine
  for a real visitor — a `broken` classification is a signal worth
  spot-checking, not a guarantee.

This drives your locally installed **Google Chrome** (via Playwright's
`channel="chrome"`) rather than downloading a separate Chromium binary, so
Chrome must already be installed on the machine running the audit.

## Project layout

```
maps_crawler/
  cli.py                argument parsing / entrypoint for the crawler
  crawler.py            ring-by-ring crawl orchestration
  grid.py                expanding-tile grid math
  places_client.py      Places API (New) HTTP client (retries, QPS throttling)
  geocode.py              address -> lat/lng via Geocoding API
  storage.py              SQLite persistence + CSV export for places
  site_audit_cli.py      argument parsing / entrypoint for website quality audits
  site_audit.py            concurrent audit orchestration (async)
  site_crawler.py          Playwright crawl: navigation, consent dismissal, screenshots, link discovery, timing
  link_checker.py          concurrent HTTP status checks for discovered links
  aesthetic_reviewer.py    Claude-vision look-scoring + vision-guided overlay dismissal
  site_audit_storage.py    SQLite persistence + CSV export for audits and link checks
vendor/
  autoconsent/             vendored DuckDuckGo autoconsent bundle (cookie-consent handling)
tests/
  test_grid.py             unit tests for the grid math
```

## Tests

```bash
pytest
```
