import csv
import json

import pytest

from calista_scorer.capture import Capture
from calista_scorer.cli import assess, parse_viewport, read_urls, write_results


class FakeCapturer:
    def __init__(self, challenge_reason=None, exc=None):
        self.challenge_reason, self.exc = challenge_reason, exc

    def capture(self, url, out_dir):
        if self.exc:
            raise self.exc
        import os

        import cv2
        import numpy as np

        os.makedirs(out_dir, exist_ok=True)
        shot = os.path.join(out_dir, "viewport_1280x800.png")
        cv2.imwrite(shot, np.full((800, 1280, 3), 200, np.uint8))
        return Capture(url=url, final_url=url, http_status=200, title="T", screenshot=shot,
                       challenge_reason=self.challenge_reason, links_found=3)


class FakeScorer:
    def score_file(self, path):
        return 5.0


def test_read_urls_skips_blanks_and_comments(tmp_path):
    f = tmp_path / "sites.txt"
    f.write_text("# header\nhttps://a.com/\n\n  https://b.com/  \n")
    assert read_urls(["https://c.com/"], str(f)) == ["https://c.com/", "https://a.com/", "https://b.com/"]


def test_parse_viewport():
    assert parse_viewport("1024x768") == (1024, 768)
    with pytest.raises(Exception):
        parse_viewport("wide")


def test_assess_scores_real_page(tmp_path):
    rec = assess(FakeCapturer(), FakeScorer(), "https://a.com/", str(tmp_path))
    assert rec["status"] == "ok"
    assert rec["score"] == 5.0
    assert rec["score_10"] == 5.5
    assert (tmp_path / "a_com" / "model_input.png").exists()


def test_assess_never_scores_challenge_page(tmp_path):
    rec = assess(FakeCapturer(challenge_reason="HTTP 403"), FakeScorer(), "https://a.com/", str(tmp_path))
    assert rec["status"] == "challenge"
    assert rec["score"] is None and rec["score_10"] is None
    assert "HTTP 403" in rec["detail"] and "5.000" in rec["detail"]


def test_assess_reports_navigation_errors(tmp_path):
    rec = assess(FakeCapturer(exc=TimeoutError("nav timeout")), FakeScorer(), "https://a.com/", str(tmp_path))
    assert rec["status"] == "error"
    assert rec["score"] is None
    assert "nav timeout" in rec["detail"]


def test_write_results_csv_and_json(tmp_path):
    results = [{"url": "https://a.com/", "status": "ok", "score": 5.0, "score_10": 5.5, "actions": []}]
    write_results(results, str(tmp_path / "r.csv"))
    rows = list(csv.DictReader(open(tmp_path / "r.csv")))
    assert rows[0]["url"] == "https://a.com/" and rows[0]["score_10"] == "5.5"
    write_results(results, str(tmp_path / "r.json"))
    assert json.load(open(tmp_path / "r.json")) == results
