import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from maps_crawler.lighthouse_storage import LighthouseStorage

PARSED_OK = {
    "final_url": "https://example.com/",
    "performance_score": 87,
    "accessibility_score": 90,
    "best_practices_score": 100,
    "seo_score": 75,
    "first_contentful_paint_ms": 1200.0,
    "largest_contentful_paint_ms": 2400.0,
    "speed_index_ms": 3100.0,
    "total_blocking_time_ms": 50.0,
    "cumulative_layout_shift": 0.01,
    "time_to_interactive_ms": 4000.0,
}


def make_storage(tmp_path):
    return LighthouseStorage(str(tmp_path / "lighthouse.db"))


def test_has_audit_only_true_after_successful_upsert(tmp_path):
    s = make_storage(tmp_path)
    assert s.has_audit("https://example.com", "mobile") is False

    s.upsert_audit("https://example.com", "mobile", PARSED_OK, "shots/a.jpg", None)
    s.commit()
    assert s.has_audit("https://example.com", "mobile") is True
    # different strategy is tracked independently
    assert s.has_audit("https://example.com", "desktop") is False
    s.close()


def test_failed_audit_does_not_count_as_done(tmp_path):
    s = make_storage(tmp_path)
    s.upsert_audit("https://broken.example", "mobile", None, None, "DNS failure")
    s.commit()

    assert s.has_audit("https://broken.example", "mobile") is False
    assert s.count_audits() == 1
    assert s.count_audits(errors_only=True) == 1
    s.close()


def test_upsert_is_idempotent_per_website_strategy(tmp_path):
    s = make_storage(tmp_path)
    s.upsert_audit("https://example.com", "mobile", None, None, "timeout")
    s.upsert_audit("https://example.com", "mobile", PARSED_OK, "shots/a.jpg", None)
    s.commit()

    assert s.count_audits() == 1
    assert s.has_audit("https://example.com", "mobile") is True
    s.close()


def test_csv_export(tmp_path):
    s = make_storage(tmp_path)
    s.upsert_audit("https://example.com", "mobile", PARSED_OK, "shots/a.jpg", None)
    s.upsert_audit("https://broken.example", "desktop", None, None, "DNS failure")
    s.commit()

    out = tmp_path / "out.csv"
    s.export_csv(str(out))
    s.close()

    lines = out.read_text().strip().splitlines()
    assert len(lines) == 3  # header + 2 rows
    assert "example.com" in lines[1] or "example.com" in lines[2]
    assert "DNS failure" in out.read_text()
