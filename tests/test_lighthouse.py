from calista_scorer.lighthouse import parse_report


def report(doc_status=200, runtime_error=None):
    r = {
        "finalDisplayedUrl": "https://a.com/",
        "configSettings": {"formFactor": "mobile"},
        "categories": {"performance": {"score": 0.55}, "accessibility": {"score": 0.86},
                       "best-practices": {"score": None}, "seo": {"score": 1}},
        "audits": {
            "metrics": {"details": {"items": [{"firstContentfulPaint": 3052, "largestContentfulPaint": 12288,
                                               "speedIndex": 38650, "totalBlockingTime": 110,
                                               "cumulativeLayoutShift": 0.1168, "interactive": 34949}]}},
            "server-response-time": {"numericValue": 51},
            "total-byte-weight": {"numericValue": 40119108},
            "network-requests": {"details": {"items": [
                {"url": "https://a.com/", "resourceType": "Document", "statusCode": doc_status},
                {"url": "https://a.com/x.css", "resourceType": "Stylesheet", "statusCode": 404}]}},
        },
    }
    if runtime_error:
        r["runtimeError"] = {"code": runtime_error}
    return r


def test_parse_report_scores_and_metrics():
    p = parse_report(report())
    assert p["status"] == "ok"
    assert (p["performance"], p["accessibility"], p["best_practices"], p["seo"]) == (55, 86, None, 100)
    assert p["fcp_ms"] == 3052 and p["lcp_ms"] == 12288 and p["tbt_ms"] == 110
    assert p["cls"] == 0.117 and p["server_response_ms"] == 51 and p["total_bytes"] == 40119108


def test_blocked_document_is_flagged():
    p = parse_report(report(doc_status=403))
    assert p["status"] == "blocked" and p["document_status"] == 403


def test_runtime_error_is_flagged():
    assert parse_report(report(runtime_error="ERRORED_DOCUMENT_REQUEST"))["status"] == "error"
