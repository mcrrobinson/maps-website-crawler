import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from maps_crawler.link_checker import LinkCheckResult
from maps_crawler.site_audit_storage import SiteAuditStorage


def make_storage(tmp_path):
    return SiteAuditStorage(str(tmp_path / "site_audit.db"))


def make_review(score=8, summary="Looks clean", issues=None, error=None):
    return SimpleNamespace(look_score=score, summary=summary, issues=issues or [], error=error)


def make_result(website, **overrides):
    result = {
        "website": website,
        "final_url": website,
        "http_status": 200,
        "pages_crawled": 3,
        "load_time_ms": 800.0,
        "desktop_screenshot_path": "shots/a_desktop.png",
        "mobile_screenshot_path": "shots/a_mobile.png",
        "desktop_review": make_review(),
        "mobile_review": make_review(score=6),
        "link_results": [],
        "error": None,
    }
    result.update(overrides)
    return result


def test_has_audit_only_true_after_successful_upsert(tmp_path):
    s = make_storage(tmp_path)
    assert s.has_audit("https://example.com") is False

    s.upsert_audit(make_result("https://example.com"))
    s.commit()
    assert s.has_audit("https://example.com") is True
    s.close()


def test_failed_audit_does_not_count_as_done(tmp_path):
    s = make_storage(tmp_path)
    s.upsert_audit(make_result("https://broken.example", error="DNS failure",
                                desktop_review=None, mobile_review=None))
    s.commit()

    assert s.has_audit("https://broken.example") is False
    assert s.count_audits() == 1
    assert s.count_audits(errors_only=True) == 1
    s.close()


def test_upsert_is_idempotent_per_website(tmp_path):
    s = make_storage(tmp_path)
    s.upsert_audit(make_result("https://example.com", error="timeout",
                                desktop_review=None, mobile_review=None))
    s.upsert_audit(make_result("https://example.com"))
    s.commit()

    assert s.count_audits() == 1
    assert s.has_audit("https://example.com") is True
    s.close()


def test_broken_link_count_rolls_up_from_link_results(tmp_path):
    s = make_storage(tmp_path)
    s.upsert_audit(make_result("https://example.com", link_results=[
        LinkCheckResult(url="https://example.com/a", source_page="https://example.com", status_code=200, classification="ok"),
        LinkCheckResult(url="https://example.com/b", source_page="https://example.com", status_code=404, classification="broken"),
        LinkCheckResult(url="https://example.com/c", source_page="https://example.com", status_code=None, classification="error", error="timeout"),
    ]))
    s.commit()

    row = s.conn.execute(
        "SELECT broken_link_count, total_link_count FROM site_audits WHERE website = ?",
        ("https://example.com",),
    ).fetchone()
    assert row == (1, 3)

    links = s.conn.execute("SELECT link_url, classification FROM site_links ORDER BY link_url").fetchall()
    assert links == [
        ("https://example.com/a", "ok"),
        ("https://example.com/b", "broken"),
        ("https://example.com/c", "error"),
    ]
    s.close()


def test_csv_export(tmp_path):
    s = make_storage(tmp_path)
    s.upsert_audit(make_result("https://example.com"))
    s.upsert_audit(make_result("https://broken.example", error="DNS failure",
                                desktop_review=None, mobile_review=None))
    s.commit()

    out = tmp_path / "out.csv"
    s.export_csv(str(out))
    s.close()

    lines = out.read_text().strip().splitlines()
    assert len(lines) == 3  # header + 2 rows
    assert "DNS failure" in out.read_text()


def test_links_csv_export(tmp_path):
    s = make_storage(tmp_path)
    s.upsert_audit(make_result("https://example.com", link_results=[
        LinkCheckResult(url="https://example.com/a", source_page="https://example.com", status_code=404, classification="broken"),
    ]))
    s.commit()

    out = tmp_path / "links.csv"
    s.export_links_csv(str(out))
    s.close()

    lines = out.read_text().strip().splitlines()
    assert len(lines) == 2  # header + 1 row
    assert "broken" in lines[1]
