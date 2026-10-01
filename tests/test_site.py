import os

from calista_scorer.capture import Capture
from calista_scorer.site import SiteOptions, assess_site

HOME = "https://a.com/"


class FakeCapturer:
    """Serves canned captures. pages maps url -> list of per-run dicts (cycled)."""

    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def capture(self, url, out_dir, name="viewport"):
        runs = self.pages[url]
        spec = runs[sum(1 for u in self.calls if u == url) % len(runs)]
        self.calls.append(url)
        if "exc" in spec:
            raise spec["exc"]
        cap = Capture(url=url, final_url=url, http_status=spec.get("status", 200), title="T",
                      challenge_reason=spec.get("challenge"), links=spec.get("links", []),
                      media_ready=spec.get("media_ready", True),
                      timings={"load_ms": spec.get("load_ms", 1000), "lcp_ms": 900})
        if not cap.challenge_reason:
            os.makedirs(out_dir, exist_ok=True)
            cap.screenshot = os.path.join(out_dir, f"{name}.png")
            with open(cap.screenshot, "w") as f:
                f.write(str(spec["score"]))
        return cap


class FakeScorer:
    def score_file(self, path):
        return float(open(path).read())


def fake_model_input(src, dst):
    pass


def run(pages, tmp_path, monkeypatch, **opts):
    monkeypatch.setattr("calista_scorer.site.save_model_input", fake_model_input)
    lh_calls = []

    def lighthouse(url, report, form_factor):
        lh_calls.append(url)
        return {"status": "ok", "performance": 70, "lcp_ms": 2500.0}

    o = SiteOptions(page_delay_s=0, **opts)
    site = assess_site(FakeCapturer(pages), FakeScorer(), HOME, str(tmp_path), o,
                       lighthouse_runner=lighthouse, robots=lambda u: (lambda x: True), log=lambda m: None)
    return site, lh_calls


def test_scores_several_pages_with_median_of_runs(tmp_path, monkeypatch):
    pages = {HOME: [{"score": 5.0, "links": [{"href": "https://a.com/menu", "nav": True}]},
                    {"score": 6.0}, {"score": 5.5}],
             "https://a.com/menu": [{"score": 3.0, "load_ms": 4000}]}
    site, lh = run(pages, tmp_path, monkeypatch, runs=3, max_pages=5)
    assert site["status"] == "ok"
    home, menu = site["pages"]
    assert home["run_scores"] == [5.0, 6.0, 5.5] and home["score"] == 5.5 and home["score_spread"] == 1.0
    assert menu["score"] == 3.0
    assert site["site_score"] == 4.25 and site["homepage_score"] == 5.5
    assert site["worst_page"] == "https://a.com/menu" and site["slowest_page_load_ms"] == 4000
    assert lh == [HOME] and site["lh_performance"] == 70


def test_challenge_homepage_blocks_site_and_skips_everything(tmp_path, monkeypatch):
    pages = {HOME: [{"challenge": "HTTP 403", "status": 403}]}
    site, lh = run(pages, tmp_path, monkeypatch, runs=3)
    assert site["status"] == "blocked"
    assert site["pages"][0]["status"] == "challenge" and site["pages"][0]["score"] is None
    assert len(site["pages"][0]["runs"]) == 1  # stops after the first challenge, no retries
    assert lh == [] and "site_score" not in site


def test_challenge_subpage_is_skipped_not_scored(tmp_path, monkeypatch):
    pages = {HOME: [{"score": 5.0, "links": [{"href": "https://a.com/members", "nav": True}]}],
             "https://a.com/members": [{"challenge": "CAPTCHA widget on near-empty page"}]}
    site, lh = run(pages, tmp_path, monkeypatch, runs=1, lighthouse="all")
    assert site["status"] == "ok" and site["pages_scored"] == 1 and site["pages_challenge"] == 1
    assert site["site_score"] == 5.0
    assert lh == [HOME]  # Lighthouse never runs on the challenge page


def test_runs_with_unpainted_media_are_excluded(tmp_path, monkeypatch):
    pages = {HOME: [{"score": 5.0}, {"score": 4.0, "media_ready": False}, {"score": 5.2}]}
    site, _ = run(pages, tmp_path, monkeypatch, runs=3, max_pages=1)
    home = site["pages"][0]
    assert home["score"] == 5.1 and "1 run(s) excluded" in home["detail"]


def test_failed_runs_dont_sink_the_page(tmp_path, monkeypatch):
    pages = {HOME: [{"exc": TimeoutError("slow")}, {"score": 5.0}]}
    site, _ = run(pages, tmp_path, monkeypatch, runs=2, max_pages=1, lighthouse="off")
    assert site["pages"][0]["score"] == 5.0 and "1 of 2 runs failed" in site["pages"][0]["detail"]


def test_lighthouse_off(tmp_path, monkeypatch):
    site, lh = run({HOME: [{"score": 5.0}]}, tmp_path, monkeypatch, runs=1, lighthouse="off")
    assert lh == [] and site["lh_pages_audited"] == 0
