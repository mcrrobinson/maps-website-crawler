import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from maps_crawler.lighthouse_client import _is_transient, parse_lighthouse_result

LIGHTHOUSE_ERROR_BODY = """
{
  "error": {
    "code": 500,
    "message": "Lighthouse returned error: FAILED_DOCUMENT_REQUEST. Requested URL couldn't be loaded."
  }
}
"""


def fake_resp(status_code, text=""):
    return SimpleNamespace(status_code=status_code, text=text)


def test_standard_retryable_status_codes_are_transient():
    for code in (429, 502, 503, 504):
        assert _is_transient(code, "") is True


def test_500_with_lighthouse_audit_failure_is_not_transient():
    assert _is_transient(500, LIGHTHOUSE_ERROR_BODY) is False


def test_500_without_lighthouse_marker_is_transient():
    assert _is_transient(500, "internal server error") is True


def test_400_is_not_transient():
    assert _is_transient(400, "invalid url") is False


def test_403_propagation_lag_is_transient():
    body = '{"error": {"code": 403, "message": "PageSpeed Insights API has not been used in project 123 before or it is disabled."}}'
    assert _is_transient(403, body) is True


def test_403_concurrency_block_is_transient():
    body = '{"error": {"code": 403, "message": "Requests to this API pagespeedonline method ... are blocked."}}'
    assert _is_transient(403, body) is True


def test_other_403s_are_not_transient():
    assert _is_transient(403, "API key not valid") is False


def _payload_with_screenshot(mime="image/jpeg"):
    return {
        "lighthouseResult": {
            "finalUrl": "https://example.com/",
            "requestedUrl": "https://example.com",
            "categories": {
                "performance": {"score": 0.87},
                "accessibility": {"score": 0.9},
                "best-practices": {"score": 1.0},
                "seo": {"score": 0.75},
            },
            "audits": {
                "metrics": {
                    "details": {
                        "items": [
                            {
                                "firstContentfulPaint": 1200,
                                "largestContentfulPaint": 2400,
                                "speedIndex": 3100,
                                "totalBlockingTime": 50,
                                "cumulativeLayoutShift": 0.01,
                                "interactive": 4000,
                            }
                        ]
                    }
                },
                "final-screenshot": {
                    "details": {"data": f"data:{mime};base64,AAAA"}
                },
            },
            "fullPageScreenshot": {
                "screenshot": {"data": f"data:{mime};base64,ZZZZ"}
            },
        }
    }


def test_parse_prefers_full_page_screenshot_and_extracts_scores():
    parsed = parse_lighthouse_result(_payload_with_screenshot())

    assert parsed["final_url"] == "https://example.com/"
    assert parsed["performance_score"] == 87
    assert parsed["accessibility_score"] == 90
    assert parsed["best_practices_score"] == 100
    assert parsed["seo_score"] == 75
    assert parsed["largest_contentful_paint_ms"] == 2400.0
    assert parsed["cumulative_layout_shift"] == 0.01
    assert parsed["screenshot_mime"] == "image/jpeg"
    assert parsed["screenshot_base64"] == "ZZZZ"  # fullPageScreenshot, not final-screenshot


def test_parse_falls_back_to_final_screenshot_when_no_full_page_shot():
    payload = _payload_with_screenshot()
    del payload["lighthouseResult"]["fullPageScreenshot"]

    parsed = parse_lighthouse_result(payload)

    assert parsed["screenshot_base64"] == "AAAA"


def test_parse_handles_missing_sections_gracefully():
    parsed = parse_lighthouse_result({"lighthouseResult": {}})

    assert parsed["performance_score"] is None
    assert parsed["screenshot_base64"] is None
