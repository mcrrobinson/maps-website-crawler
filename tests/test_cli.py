import csv
import json

import pytest

from calista_scorer.cli import main, page_rows, parse_viewport, read_urls, write_results

SITE = {"url": "https://a.com/", "status": "ok", "site_score": 5.0, "pages_scored": 1,
        "pages": [{"url": "https://a.com/", "status": "ok", "score": 5.0, "score_10": 5.5,
                   "run_scores": [4.9, 5.0, 5.1], "runs": [{}],
                   "lighthouse": {"status": "ok", "performance": 80, "lcp_ms": 2100.0}}]}


def test_read_urls_skips_blanks_and_comments(tmp_path):
    f = tmp_path / "sites.txt"
    f.write_text("# header\nhttps://a.com/\n\n  https://b.com/  \n")
    assert read_urls(["https://c.com/"], str(f)) == ["https://c.com/", "https://a.com/", "https://b.com/"]


def test_parse_viewport():
    assert parse_viewport("1024x768") == (1024, 768)
    with pytest.raises(Exception):
        parse_viewport("wide")


def test_page_rows_flatten_lighthouse_and_runs():
    row = page_rows([SITE])[0]
    assert row["site"] == "https://a.com/"
    assert row["run_scores"] == "4.9 5.0 5.1"
    assert row["lh_status"] == "ok" and row["lh_performance"] == 80 and row["lh_lcp_ms"] == 2100.0
    assert "runs" not in row and "lighthouse" not in row


def test_write_results(tmp_path):
    write_results([SITE], str(tmp_path))
    assert json.load(open(tmp_path / "results.json"))[0]["site_score"] == 5.0
    sites = list(csv.DictReader(open(tmp_path / "sites.csv")))
    pages = list(csv.DictReader(open(tmp_path / "pages.csv")))
    assert sites[0]["site_score"] == "5.0"
    assert pages[0]["lh_performance"] == "80"


def test_runs_must_be_positive():
    with pytest.raises(SystemExit):
        main(["score", "https://a.com/", "--runs", "0"])
