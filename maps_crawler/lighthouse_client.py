"""Thin client around the Google PageSpeed Insights API (hosted Lighthouse).

Docs: https://developers.google.com/speed/docs/insights/v5/get-started
"""

import threading
import time

import requests

PSI_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"

CATEGORIES = ["performance", "accessibility", "best-practices", "seo"]

# 429/502/503/504 are worth retrying — the request never really landed.
# A 500 usually means Lighthouse itself failed to audit the page (broken
# site, DNS failure, redirect loop, ...); retrying won't fix that unless the
# body doesn't actually say so.
RETRYABLE_STATUS_CODES = {429, 502, 503, 504}


class LighthouseApiError(RuntimeError):
    pass


def _is_transient(status_code: int, body_text: str) -> bool:
    if status_code in RETRYABLE_STATUS_CODES:
        return True
    if status_code == 500 and "Lighthouse returned error" not in body_text:
        return True
    # Right after enabling an API in Google Cloud Console, it can take a few
    # minutes to propagate; during that window calls intermittently 403 even
    # though the API is actually enabled. Worth retrying. (A 403 caused by
    # API-restricted key settings won't match this and is treated as fatal,
    # since retrying can't fix that.)
    if status_code == 403 and "has not been used in project" in body_text:
        return True
    # Seen in practice under high concurrency: a burst of simultaneous
    # in-flight requests trips a stricter abuse guard than the documented
    # QPS quota, especially soon after a key's restrictions were changed.
    # It self-resolves — worth retrying with backoff.
    if status_code == 403 and "are blocked" in body_text:
        return True
    return False


class _RateLimiter:
    """Thread-safe pacing so concurrent callers stay under a shared QPS cap."""

    def __init__(self, qps: float):
        self.min_interval = 1.0 / qps if qps > 0 else 0.0
        self._lock = threading.Lock()
        self._last_request_at = 0.0

    def wait(self):
        with self._lock:
            wait = self.min_interval - (time.monotonic() - self._last_request_at)
            if wait > 0:
                time.sleep(wait)
            self._last_request_at = time.monotonic()


class LighthouseClient:
    def __init__(
        self,
        api_key: str,
        qps: float = 3.0,
        max_retries: int = 4,
        timeout: float = 90.0,
        session: requests.Session | None = None,
    ):
        self.api_key = api_key
        self.limiter = _RateLimiter(qps)
        self.max_retries = max_retries
        self.timeout = timeout
        self.session = session or requests.Session()

    def run_audit(self, url: str, strategy: str = "mobile") -> dict:
        """Runs a hosted Lighthouse audit for `url`. Thread-safe — the shared
        rate limiter paces all callers, so this can be called from a pool."""
        params = {
            "url": url,
            "key": self.api_key,
            "strategy": strategy,
            "category": CATEGORIES,
        }

        backoff = 2.0
        last_error = None
        for attempt in range(self.max_retries + 1):
            self.limiter.wait()
            try:
                resp = self.session.get(PSI_URL, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = exc
            else:
                if resp.status_code == 200:
                    return resp.json()
                if not _is_transient(resp.status_code, resp.text):
                    raise LighthouseApiError(
                        f"PSI request failed ({resp.status_code}) for {url} [{strategy}]: "
                        f"{resp.text[:300]}"
                    )
                last_error = LighthouseApiError(
                    f"PSI request failed ({resp.status_code}) for {url} [{strategy}]: "
                    f"{resp.text[:300]}"
                )

            if attempt < self.max_retries:
                time.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

        raise LighthouseApiError(
            f"PSI request failed after {self.max_retries + 1} attempts for {url} [{strategy}]"
        ) from last_error


def _score_pct(categories: dict, key: str):
    cat = categories.get(key)
    if not cat or cat.get("score") is None:
        return None
    return round(cat["score"] * 100)


def _metric(items: dict, key: str):
    value = items.get(key)
    return None if value is None else float(value)


def parse_lighthouse_result(payload: dict) -> dict:
    """Extracts scores, core metrics, and a full-page screenshot from a raw
    PSI `runPagespeed` response."""
    lr = payload.get("lighthouseResult") or {}
    categories = lr.get("categories") or {}
    audits = lr.get("audits") or {}

    metrics_items = ((audits.get("metrics") or {}).get("details") or {}).get("items") or [{}]
    metrics = metrics_items[0] if metrics_items else {}

    full_page_shot = (lr.get("fullPageScreenshot") or {}).get("screenshot") or {}
    data_uri = full_page_shot.get("data") or (
        (audits.get("final-screenshot") or {}).get("details") or {}
    ).get("data")

    screenshot_b64 = None
    screenshot_mime = None
    if data_uri and data_uri.startswith("data:"):
        header, _, encoded = data_uri.partition(",")
        screenshot_mime = header.removeprefix("data:").split(";")[0]
        screenshot_b64 = encoded

    return {
        "final_url": lr.get("finalUrl") or lr.get("requestedUrl"),
        "performance_score": _score_pct(categories, "performance"),
        "accessibility_score": _score_pct(categories, "accessibility"),
        "best_practices_score": _score_pct(categories, "best-practices"),
        "seo_score": _score_pct(categories, "seo"),
        "first_contentful_paint_ms": _metric(metrics, "firstContentfulPaint"),
        "largest_contentful_paint_ms": _metric(metrics, "largestContentfulPaint"),
        "speed_index_ms": _metric(metrics, "speedIndex"),
        "total_blocking_time_ms": _metric(metrics, "totalBlockingTime"),
        "cumulative_layout_shift": _metric(metrics, "cumulativeLayoutShift"),
        "time_to_interactive_ms": _metric(metrics, "interactive"),
        "screenshot_base64": screenshot_b64,
        "screenshot_mime": screenshot_mime,
    }
