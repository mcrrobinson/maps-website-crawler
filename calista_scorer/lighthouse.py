"""Run Google Lighthouse locally (the pinned npm package in package.json) and parse its report.

Lighthouse defaults to mobile emulation with simulated throttling (a mid-range phone on a
slow 4G connection), which is what PageSpeed Insights reports. Use form_factor="desktop"
for desktop settings.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL_BIN = os.path.join(ROOT, "node_modules", ".bin", "lighthouse")
CATEGORIES = ["performance", "accessibility", "best-practices", "seo"]


class LighthouseUnavailable(RuntimeError):
    pass


def lighthouse_bin() -> str:
    if os.path.exists(LOCAL_BIN):
        return LOCAL_BIN
    found = shutil.which("lighthouse")
    if not found:
        raise LighthouseUnavailable("lighthouse not found; run `npm install` in the repo root")
    return found


def _score_pct(categories: dict, key: str):
    cat = categories.get(key)
    if not cat or cat.get("score") is None:
        return None
    return round(cat["score"] * 100)


def _num(value):
    return None if value is None else round(float(value), 3)


def parse_report(report: dict) -> dict:
    """Pull scores and core metrics out of a Lighthouse JSON report."""
    categories = report.get("categories") or {}
    audits = report.get("audits") or {}
    items = ((audits.get("metrics") or {}).get("details") or {}).get("items") or [{}]
    m = items[0] if items else {}
    doc_status = None
    for req in ((audits.get("network-requests") or {}).get("details") or {}).get("items") or []:
        if req.get("resourceType") == "Document":
            doc_status = req.get("statusCode")
            break
    runtime_error = (report.get("runtimeError") or {}).get("code")
    if runtime_error:
        status = "error"
    elif doc_status is not None and doc_status >= 400:
        status = "blocked"
    else:
        status = "ok"
    return {
        "status": status,
        "runtime_error": runtime_error,
        "document_status": doc_status,
        "final_url": report.get("finalDisplayedUrl") or report.get("finalUrl"),
        "form_factor": (report.get("configSettings") or {}).get("formFactor"),
        "performance": _score_pct(categories, "performance"),
        "accessibility": _score_pct(categories, "accessibility"),
        "best_practices": _score_pct(categories, "best-practices"),
        "seo": _score_pct(categories, "seo"),
        "fcp_ms": _num(m.get("firstContentfulPaint")),
        "lcp_ms": _num(m.get("largestContentfulPaint")),
        "speed_index_ms": _num(m.get("speedIndex")),
        "tbt_ms": _num(m.get("totalBlockingTime")),
        "cls": _num(m.get("cumulativeLayoutShift")),
        "tti_ms": _num(m.get("interactive")),
        "server_response_ms": _num((audits.get("server-response-time") or {}).get("numericValue")),
        "total_bytes": _num((audits.get("total-byte-weight") or {}).get("numericValue")),
    }


def run_lighthouse(url: str, report_path: str, form_factor: str = "mobile", timeout_s: int = 180) -> dict:
    os.makedirs(os.path.dirname(os.path.abspath(report_path)), exist_ok=True)
    if os.path.exists(report_path):
        os.remove(report_path)  # never read a stale report from an earlier run
    cmd = [lighthouse_bin(), url, "--output=json", f"--output-path={report_path}", "--quiet",
           "--chrome-flags=--headless=new", f"--only-categories={','.join(CATEGORIES)}"]
    if form_factor == "desktop":
        cmd.append("--preset=desktop")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return {"status": "error", "runtime_error": f"timed out after {timeout_s}s"}
    if not os.path.exists(report_path):
        return {"status": "error", "runtime_error": (proc.stderr or proc.stdout).strip()[-300:] or
                f"lighthouse exited {proc.returncode}"}
    with open(report_path) as f:
        result = parse_report(json.load(f))
    result["report"] = report_path
    return result
