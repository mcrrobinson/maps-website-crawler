# calista_scorer

Scores how good a website looks using the **Calista rating-based CNN**
([Delitzas et al., IJHCS 2023](https://github.com/calista-ai/website-aesthetics-research)).
The model was trained on human aesthetic ratings of website screenshots. Give it a URL and it
returns a 1-9 rating. There is no LLM involved and no API cost; it runs on CPU in about
5-10 seconds per site.

## How it works

1. Loads the homepage in headless Chrome (Playwright, `channel="chrome"`).
2. Checks whether the page is a bot check or error page. A page is skipped and **not scored**
   if any of these is true:
   - it returned HTTP ≥ 400
   - its title looks like a challenge ("Just a moment...", "captcha", "access denied", ...)
   - it is nearly empty and contains challenge text or a Turnstile, reCAPTCHA or hCaptcha widget

   A real site that only has a CAPTCHA on its contact form is still scored.
3. Clicks obvious cookie-accept or popup-close buttons ("Accept all", "Close", "No thanks",
   ...). It never submits forms and never touches CAPTCHAs.
4. Takes a 1280x800 above-the-fold screenshot and resizes it to 256x192, the model's input
   size. It then scores the screenshot with the CNN.

Outputs:
- `score` is the native 1-9 rating.
- `score_10` is the same rating mapped onto 1-10.

In practice scores cluster between about 2.5 and 6.5, so use them for **ranking** sites rather
than as absolute grades.

## Setup

```bash
uv venv --python 3.12 .venv          # or: python3.12 -m venv .venv
uv pip install -r requirements.txt   # or: .venv/bin/pip install -r requirements.txt
.venv/bin/python -m calista_scorer download-weights   # ~96 MB, saved to weights/
```

You also need Google Chrome installed, because the browser is launched with `channel="chrome"`.

## Usage

```bash
# Score sites (results.csv plus screenshots/<site>/ with the screenshots and model_input.png)
.venv/bin/python -m calista_scorer score https://www.katzsdelicatessen.com/ https://gjelina.com/
.venv/bin/python -m calista_scorer score --sites-file sites.txt --out results.json

# Score screenshots you already have
.venv/bin/python -m calista_scorer score-image shot1.png shot2.png
```

Options for `score`:
- `--viewport 1024x768`: the model was trained on 1024x768 screenshots.
- `--headful`: show the browser window.
- `--artifacts-dir DIR`: choose where screenshots are saved.

Each result has a `status`:
- `ok`: scored.
- `challenge`: a bot check or error page. It isn't scored, but `detail` includes the raw model
  output for reference.
- `error`: navigation failed. The command exits with code 1 if any site errors.

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
  contact form, a soft challenge page and a 404.
