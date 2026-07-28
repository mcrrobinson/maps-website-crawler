"""Orchestrates concurrent site-quality audits: crawl (Playwright) + link
validation (requests) + aesthetic scoring (Claude vision) per website,
checkpointing results to storage as each site finishes.

The crawl step is async (site_crawler.py's Playwright automation requires
the async API — see its module docstring). Link-checking and vision-scoring
are blocking calls run via asyncio.to_thread so they don't stall the event
loop, while a semaphore bounds how many sites are crawled at once — real
browser tabs are much heavier than the thread-pooled HTTP calls the old
Lighthouse pipeline used.
"""

import asyncio
import hashlib
from pathlib import Path
from typing import Callable, Optional

from .aesthetic_reviewer import AestheticReviewer
from .link_checker import check_links
from .site_crawler import SiteCrawler

ProgressCallback = Callable[[str, bool, Optional[str]], None]


def plan_jobs(storage, websites: list, force: bool = False) -> list:
    """Returns the websites that still need an audit."""
    return [w for w in websites if force or not storage.has_audit(w)]


def _screenshot_filename(website: str, variant: str) -> str:
    digest = hashlib.sha1(website.encode()).hexdigest()[:16]
    return f"{digest}_{variant}.png"


async def _audit_one(
    website: str,
    crawler: SiteCrawler,
    reviewer: AestheticReviewer,
    max_pages: int,
    screenshot_dir: Path,
) -> dict:
    crawl_result = await crawler.crawl(
        website, max_pages=max_pages, vision_overlay_check=reviewer.check_overlay
    )
    if crawl_result.error:
        return {"website": website, "error": crawl_result.error}

    desktop_path = mobile_path = None
    if crawl_result.desktop_screenshot:
        desktop_path = screenshot_dir / _screenshot_filename(website, "desktop")
        desktop_path.write_bytes(crawl_result.desktop_screenshot)
    if crawl_result.mobile_screenshot:
        mobile_path = screenshot_dir / _screenshot_filename(website, "mobile")
        mobile_path.write_bytes(crawl_result.mobile_screenshot)

    desktop_review = mobile_review = None
    if crawl_result.desktop_screenshot:
        desktop_review = await asyncio.to_thread(reviewer.score_screenshot, crawl_result.desktop_screenshot)
    if crawl_result.mobile_screenshot:
        mobile_review = await asyncio.to_thread(reviewer.score_screenshot, crawl_result.mobile_screenshot)

    link_results = await asyncio.to_thread(check_links, crawl_result.links)

    return {
        "website": website,
        "final_url": crawl_result.final_url,
        "http_status": crawl_result.http_status,
        "pages_crawled": crawl_result.pages_crawled,
        "load_time_ms": crawl_result.load_time_ms,
        "desktop_screenshot_path": str(desktop_path) if desktop_path else None,
        "mobile_screenshot_path": str(mobile_path) if mobile_path else None,
        "desktop_review": desktop_review,
        "mobile_review": mobile_review,
        "link_results": link_results,
        "error": None,
    }


async def audit_websites_async(
    storage,
    crawler,
    reviewer: AestheticReviewer,
    jobs: list,
    screenshot_dir: str,
    max_pages: int = 15,
    max_concurrency: int = 4,
    on_progress: Optional[ProgressCallback] = None,
) -> None:
    """`crawler` is any already-started object exposing SiteCrawler's async
    `crawl()` method — injected rather than constructed here so tests can
    substitute a fake without launching a real browser."""
    screenshot_path = Path(screenshot_dir)
    screenshot_path.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(max_concurrency)

    async def run(website):
        async with semaphore:
            try:
                result = await _audit_one(website, crawler, reviewer, max_pages, screenshot_path)
            except Exception as exc:
                result = {"website": website, "error": f"{type(exc).__name__}: {exc}"}

            storage.upsert_audit(result)
            storage.commit()

            if on_progress:
                on_progress(website, result.get("error") is None, result.get("error"))

    await asyncio.gather(*(run(website) for website in jobs))


def run_site_audit(
    storage,
    reviewer: AestheticReviewer,
    jobs: list,
    screenshot_dir: str,
    max_pages: int = 15,
    max_concurrency: int = 4,
    on_progress: Optional[ProgressCallback] = None,
    headless: bool = True,
) -> None:
    """Synchronous entrypoint for the CLI — owns the real SiteCrawler's
    (and therefore the real browser's) lifecycle."""

    async def _main():
        async with SiteCrawler(headless=headless) as crawler:
            await audit_websites_async(
                storage, crawler, reviewer, jobs, screenshot_dir,
                max_pages=max_pages, max_concurrency=max_concurrency, on_progress=on_progress,
            )

    asyncio.run(_main())
