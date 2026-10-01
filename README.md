# calista_scorer

Assesses websites in two ways:
- **How it looks:** the **Calista rating-based CNN**
  ([Delitzas et al., IJHCS 2023](https://github.com/calista-ai/website-aesthetics-research)),
  trained on human aesthetic ratings of website screenshots, rates each page from 1 to 9.
- **How fast it loads:** timings measured during each visit, plus a local **Google Lighthouse**
  audit.

Everything runs locally, with no LLM and no API keys.

## How it works

For each site:

1. **Homepage.** Loads it in headless Chrome (Playwright, `channel="chrome"`) and waits until
   the images and videos in view, including Vimeo/YouTube players in iframes, have painted.
2. **Bot checks.** If the page is a bot check or error page, the tool waits up to 10 s for the
   site's own check to finish by itself. It never clicks anything during that wait. If the page
   is still a check after that, it is **skipped**: it is never sent to Calista or Lighthouse, and
   only an evidence screenshot is kept. If the homepage is skipped, the site is reported as
   `blocked`. A page counts as a check if any of these is true:
   - it returned HTTP ≥ 400
   - its title looks like a challenge ("Just a moment...", "captcha", "access denied", ...)
   - it is nearly empty and contains challenge text or a Turnstile, reCAPTCHA or hCaptcha widget

   A real site that only has a CAPTCHA on its contact form is still scored.
3. **Popups and timings.** Records the visit's load timings: TTFB, FCP, LCP, DOMContentLoaded
   and load. It then clicks obvious cookie-accept or popup-close buttons. It never submits forms
   and never touches CAPTCHAs.
4. **Screenshot.** Takes a 1280x800 above-the-fold screenshot, resizes it to 256x192 and scores
   it with the CNN.
5. **Repeat.** Steps 1-4 run `--runs` times (default 3), each in a fresh browser, and the page
   score is the median. This evens out carousels, video heroes and slow-loading images. A run
   whose hero media never painted is left out if another run did paint.
6. **More pages.** Picks up to `--max-pages` pages (default 5, homepage included) from the
   homepage's links:
   - same site only, links in the header/nav first;
   - skips files, login/cart/admin pages and anything robots.txt disallows;
   - waits 1 s between page loads.

   Each page is scored as in steps 1-5.
7. **Lighthouse.** Runs Lighthouse on the homepage (`--lighthouse home`, the default), on every
   scored page (`all`), or not at all (`off`). It uses mobile emulation with simulated slow 4G,
   the same settings as PageSpeed Insights, unless you pass `--lighthouse-form-factor desktop`.

In practice Calista's scores cluster between about 2.5 and 6.5, so use them for **ranking**
sites rather than as absolute grades.

### Why there's no CAPTCHA solving

The browser sends Chrome's normal user-agent instead of the headless build's, which contains
`HeadlessChrome`. Some firewalls reject that token outright: royalnavy.mod.uk returns a
Cloudflare 403 with it and the real page without it. Lighthouse does the same.

Beyond that, the tool doesn't solve CAPTCHAs, use stealth plugins or fingerprint spoofing, or
use CAPTCHA-solving services. A site that still shows a challenge has chosen to keep automated
visitors out, so the tool reports it as `blocked` instead of scoring a challenge page.
Lighthouse only runs on pages the tool could load itself, so blocked sites get no Lighthouse
numbers either.

## Setup

```bash
uv venv --python 3.12 .venv          # or: python3.12 -m venv .venv
uv pip install -r requirements.txt   # or: .venv/bin/pip install -r requirements.txt
.venv/bin/python -m calista_scorer download-weights   # ~96 MB, saved to weights/
npm install                          # Lighthouse, pinned in package.json (Node >= 22.19)
```

You also need Google Chrome installed. Playwright launches it with `channel="chrome"`, and
Lighthouse uses the same install.

## Usage

```bash
.venv/bin/python -m calista_scorer score https://www.katzsdelicatessen.com/ https://gjelina.com/
.venv/bin/python -m calista_scorer score --sites-file sites.txt --max-pages 8 --lighthouse all

# Quick look: homepage only, one run, no Lighthouse
.venv/bin/python -m calista_scorer score https://gjelina.com/ --max-pages 1 --runs 1 --lighthouse off

# Score screenshots you already have
.venv/bin/python -m calista_scorer score-image shot1.png shot2.png
```

Results go to `--out-dir` (default `results/`) and are rewritten after every site, so an
interrupted run keeps what it has done:

| File | Contents |
|---|---|
| `sites.csv` | One row per site: `site_score` (mean of page scores), `homepage_score`, min/max page score, `worst_page`, load times, Lighthouse scores and metrics (`lh_*`, median over audited pages) |
| `pages.csv` | One row per page: median score, the individual `run_scores` and their spread, observed timings, Lighthouse results if audited |
| `results.json` | Everything, including each run's actions (clicks, waits) |
| `screenshots/<site>/<page>/` | Every run's screenshot, the 256x192 `model_input.png`, and `*_challenge.png` evidence for skipped pages |
| `lighthouse/` | Full Lighthouse JSON reports |

Statuses:

| Level | Status | Meaning |
|---|---|---|
| site | `ok` | assessed |
| site | `blocked` | the homepage was a bot check or error page |
| site | `error` | the site couldn't be loaded |
| page | `ok` | scored |
| page | `challenge` | skipped, never scored |
| page | `error` | every run failed |

The command exits with code 1 if any site errors.

Other options:
- `--viewport 1024x768`: the model was trained on 1024x768 screenshots.
- `--lighthouse-form-factor desktop`: run Lighthouse with desktop settings.
- `--ignore-robots`: don't check robots.txt.
- `--headful`: show the browser window.

### Timing

Expect about 1-4 minutes per site with the defaults. Heavy pages wait up to 12 s for the
network to go quiet and up to 8 s for media to paint, and a Lighthouse audit takes 20-60 s.

The observed timings in `pages.csv` come from your machine and connection with no throttling.
Lighthouse's numbers are simulated slow-mobile figures. Use the Lighthouse numbers to compare
sites, and the observed ones as a sanity check.

## The model port

The authors' code targets TF 1.14 / Keras 2.2.5 / Python 3.6. `calista_scorer/model.py` rebuilds
the architecture from `rating-based-models/approach1/model1.ipynb`. That includes the custom LRN
and the grouped conv splits, ported to TF 2 / Keras 3. The authors' released
`calista_rating_based.h5` weights are loaded by layer name.

`scripts/validate_port.py` checks the port. The check needs torch and the authors' repos cloned
into `validation/` (see the script's docstring). Results:
- The TF port and an independent PyTorch port agree to within about 3e-6.
- On the authors' 24 out-of-sample screenshots, our outputs correlate r = 0.999 with their
  published predictions. Both correlate equally with human ratings (r = 0.769).
- Our outputs run about 0.48 lower than the published ones, probably because the published
  predictions came from a slightly different checkpoint. Ranking is unaffected.

## Tests

```bash
.venv/bin/python -m pytest -q
```

Some tests need extra setup and skip automatically without it:
- the model test needs the downloaded weights;
- the browser tests need Chrome. They serve local pages: a cookie banner, a CAPTCHA on a
  contact form, a soft challenge page, a challenge that clears by itself, a 404 and a video
  that never loads.
