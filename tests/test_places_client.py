import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from maps_crawler.places_client import _is_transient

SERVICE_DISABLED_BODY = """
{
  "error": {
    "code": 403,
    "status": "PERMISSION_DENIED",
    "details": [{"reason": "SERVICE_DISABLED"}]
  }
}
"""


def fake_resp(status_code, text=""):
    return SimpleNamespace(status_code=status_code, text=text)


def test_standard_retryable_status_codes_are_transient():
    for code in (429, 500, 502, 503, 504):
        assert _is_transient(fake_resp(code)) is True


def test_403_service_disabled_is_transient():
    assert _is_transient(fake_resp(403, SERVICE_DISABLED_BODY)) is True


def test_other_403s_are_not_transient():
    assert _is_transient(fake_resp(403, "API key invalid")) is False


def test_404_is_not_transient():
    assert _is_transient(fake_resp(404, "not found")) is False
