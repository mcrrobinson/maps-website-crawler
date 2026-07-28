"""Concurrent HTTP status checks for links discovered during a site crawl.

Uses a thread pool (not raw request rate) since these checks are
latency-bound, the same reasoning the old PSI client used for audits.
"""

import dataclasses
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import requests

TIMEOUT = 10.0
_RETRY_AS_GET_STATUSES = {405, 501}
_USER_AGENT = "Mozilla/5.0 (compatible; MapsCrawlerSiteAudit/1.0)"


@dataclasses.dataclass
class LinkCheckResult:
    url: str
    source_page: str
    status_code: Optional[int]
    classification: str  # "ok" | "redirect" | "broken" | "error"
    error: Optional[str] = None


def _classify(status_code: int, redirected: bool) -> str:
    if 200 <= status_code < 400:
        return "redirect" if redirected else "ok"
    return "broken"


def check_one(url: str, source_page: str, session: requests.Session) -> LinkCheckResult:
    try:
        resp = session.head(url, timeout=TIMEOUT, allow_redirects=True)
        if resp.status_code in _RETRY_AS_GET_STATUSES:
            resp.close()
            resp = session.get(url, timeout=TIMEOUT, allow_redirects=True, stream=True)
            resp.close()
        return LinkCheckResult(
            url=url, source_page=source_page, status_code=resp.status_code,
            classification=_classify(resp.status_code, bool(resp.history)),
        )
    except requests.RequestException as exc:
        return LinkCheckResult(
            url=url, source_page=source_page, status_code=None,
            classification="error", error=f"{type(exc).__name__}: {exc}",
        )


def check_links(links: dict, max_workers: int = 16) -> list:
    """`links` maps link_url -> the page it was discovered on. Returns one
    LinkCheckResult per link."""
    session = requests.Session()
    session.headers.update({"User-Agent": _USER_AGENT})
    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(check_one, url, source_page, session)
            for url, source_page in links.items()
        ]
        for future in as_completed(futures):
            results.append(future.result())
    return results
