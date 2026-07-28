import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from maps_crawler.link_checker import LinkCheckResult
from maps_crawler.site_audit import audit_websites_async, plan_jobs
from maps_crawler.site_audit_storage import SiteAuditStorage
from maps_crawler.site_crawler import SiteCrawlResult


def make_storage(tmp_path):
    return SiteAuditStorage(str(tmp_path / "site_audit.db"))


def test_plan_jobs_skips_completed_sites(tmp_path):
    s = make_storage(tmp_path)
    s.upsert_audit({
        "website": "https://a.example", "final_url": "https://a.example", "http_status": 200,
        "pages_crawled": 1, "load_time_ms": 500.0, "desktop_screenshot_path": None,
        "mobile_screenshot_path": None, "desktop_review": None, "mobile_review": None,
        "link_results": [], "error": None,
    })
    s.commit()

    jobs = plan_jobs(s, ["https://a.example", "https://b.example"])
    assert jobs == ["https://b.example"]
    s.close()


def test_plan_jobs_force_reruns_everything(tmp_path):
    s = make_storage(tmp_path)
    s.upsert_audit({
        "website": "https://a.example", "final_url": "https://a.example", "http_status": 200,
        "pages_crawled": 1, "load_time_ms": 500.0, "desktop_screenshot_path": None,
        "mobile_screenshot_path": None, "desktop_review": None, "mobile_review": None,
        "link_results": [], "error": None,
    })
    s.commit()

    jobs = plan_jobs(s, ["https://a.example"], force=True)
    assert jobs == ["https://a.example"]
    s.close()


class FakeCrawler:
    def __init__(self, results):
        self._results = results

    async def crawl(self, website, max_pages, vision_overlay_check):
        return self._results[website]


class FakeReviewer:
    def score_screenshot(self, image_bytes, media_type="image/png"):
        return SimpleNamespace(look_score=7, summary="Fine", issues=[], error=None)

    async def check_overlay(self, image_bytes, media_type="image/png"):
        return None


def test_audit_websites_async_wires_crawl_links_and_review_into_storage(tmp_path):
    storage = make_storage(tmp_path)
    crawler = FakeCrawler({
        "https://good.example": SiteCrawlResult(
            website="https://good.example", final_url="https://good.example", http_status=200,
            load_time_ms=900.0, pages_crawled=2,
            desktop_screenshot=b"desktop-bytes", mobile_screenshot=b"mobile-bytes",
            links={"https://good.example/broken": "https://good.example"},
        ),
        "https://bad.example": SiteCrawlResult(website="https://bad.example", error="TimeoutError: nav timeout"),
    })
    reviewer = FakeReviewer()

    seen_progress = []

    def on_progress(website, ok, error):
        seen_progress.append((website, ok, error))

    import maps_crawler.site_audit as site_audit_module

    def fake_check_links(links, max_workers=16):
        return [LinkCheckResult(url=u, source_page=s, status_code=404, classification="broken") for u, s in links.items()]

    orig_check_links = site_audit_module.check_links
    site_audit_module.check_links = fake_check_links
    try:
        asyncio.run(audit_websites_async(
            storage, crawler, reviewer,
            ["https://good.example", "https://bad.example"],
            screenshot_dir=str(tmp_path / "shots"),
            on_progress=on_progress,
        ))
    finally:
        site_audit_module.check_links = orig_check_links

    storage.commit()
    assert storage.has_audit("https://good.example") is True
    assert storage.has_audit("https://bad.example") is False

    row = storage.conn.execute(
        "SELECT desktop_look_score, broken_link_count, total_link_count FROM site_audits WHERE website = ?",
        ("https://good.example",),
    ).fetchone()
    assert row == (7, 1, 1)

    assert (tmp_path / "shots").exists()
    screenshots = list((tmp_path / "shots").glob("*.png"))
    assert len(screenshots) == 2  # desktop + mobile

    assert ("https://good.example", True, None) in seen_progress
    assert ("https://bad.example", False, "TimeoutError: nav timeout") in seen_progress
    storage.close()
