import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests

from maps_crawler.link_checker import check_links, check_one


class FakeSession:
    def __init__(self, responses):
        # responses: dict url -> list of (method, status_code, history) or an exception
        self._responses = responses
        self.headers = {}

    def head(self, url, timeout, allow_redirects):
        return self._respond(url)

    def get(self, url, timeout, allow_redirects, stream):
        return self._respond(url)

    def _respond(self, url):
        outcome = self._responses[url]
        if isinstance(outcome, Exception):
            raise outcome
        status_code, history = outcome
        return SimpleNamespace(status_code=status_code, history=history, close=lambda: None)


def test_ok_status_classified_ok():
    session = FakeSession({"https://a.example": (200, [])})
    result = check_one("https://a.example", "https://source.example", session)
    assert result.classification == "ok"
    assert result.status_code == 200
    assert result.error is None


def test_redirected_ok_status_classified_redirect():
    session = FakeSession({"https://a.example": (200, [SimpleNamespace()])})
    result = check_one("https://a.example", "https://source.example", session)
    assert result.classification == "redirect"


def test_4xx_and_5xx_classified_broken():
    session = FakeSession({"https://a.example": (404, [])})
    assert check_one("https://a.example", "src", session).classification == "broken"

    session = FakeSession({"https://b.example": (500, [])})
    assert check_one("https://b.example", "src", session).classification == "broken"


def test_connection_failure_classified_error():
    session = FakeSession({"https://a.example": requests.ConnectionError("DNS failure")})
    result = check_one("https://a.example", "src", session)
    assert result.classification == "error"
    assert result.status_code is None
    assert "DNS failure" in result.error


def test_check_links_runs_all_links_concurrently(monkeypatch):
    links = {"https://a.example": "https://src", "https://b.example": "https://src"}

    class _FakeSessionCtor:
        def __call__(self):
            return FakeSession({"https://a.example": (200, []), "https://b.example": (404, [])})

    monkeypatch.setattr("maps_crawler.link_checker.requests.Session", _FakeSessionCtor())

    results = check_links(links, max_workers=2)
    by_url = {r.url: r for r in results}
    assert by_url["https://a.example"].classification == "ok"
    assert by_url["https://b.example"].classification == "broken"
