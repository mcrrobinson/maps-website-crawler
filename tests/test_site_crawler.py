import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from maps_crawler.site_crawler import normalize_url, same_site


def test_normalize_url_adds_https_scheme_when_missing():
    assert normalize_url("example.com") == "https://example.com"


def test_normalize_url_leaves_existing_scheme_alone():
    assert normalize_url("http://example.com") == "http://example.com"
    assert normalize_url("https://example.com") == "https://example.com"


def test_same_site_ignores_www_prefix():
    assert same_site("https://www.example.com/a", "https://example.com/b") is True


def test_same_site_treats_different_hosts_as_different():
    assert same_site("https://example.com", "https://other.example.com") is False


def test_same_site_handles_empty_host():
    assert same_site("", "https://example.com") is False
