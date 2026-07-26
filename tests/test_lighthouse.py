import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from maps_crawler.lighthouse import _screenshot_filename, plan_jobs
from maps_crawler.lighthouse_storage import LighthouseStorage

PARSED_OK = {"performance_score": 90}


def make_storage(tmp_path):
    return LighthouseStorage(str(tmp_path / "lighthouse.db"))


def test_plan_jobs_skips_completed_pairs(tmp_path):
    s = make_storage(tmp_path)
    s.upsert_audit("https://a.example", "mobile", PARSED_OK, None, None)
    s.commit()

    jobs = plan_jobs(s, ["https://a.example", "https://b.example"], ["mobile", "desktop"])

    assert ("https://a.example", "mobile") not in jobs
    assert ("https://a.example", "desktop") in jobs
    assert ("https://b.example", "mobile") in jobs
    assert ("https://b.example", "desktop") in jobs
    assert len(jobs) == 3
    s.close()


def test_plan_jobs_force_reruns_everything(tmp_path):
    s = make_storage(tmp_path)
    s.upsert_audit("https://a.example", "mobile", PARSED_OK, None, None)
    s.commit()

    jobs = plan_jobs(s, ["https://a.example"], ["mobile"], force=True)

    assert jobs == [("https://a.example", "mobile")]
    s.close()


def test_screenshot_filename_uses_correct_extension_per_mime():
    assert _screenshot_filename("https://a.example", "mobile", "image/webp").endswith(".webp")
    assert _screenshot_filename("https://a.example", "mobile", "image/png").endswith(".png")
    assert _screenshot_filename("https://a.example", "mobile", "image/jpeg").endswith(".jpg")
    assert _screenshot_filename("https://a.example", "mobile", None).endswith(".img")
