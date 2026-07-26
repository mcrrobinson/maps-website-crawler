import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from maps_crawler.grid import Tile
from maps_crawler.storage import Storage

PLACE_WITH_SITE = {
    "id": "abc123",
    "displayName": {"text": "Joe's Coffee"},
    "formattedAddress": "123 Main St",
    "location": {"latitude": 37.001, "longitude": -122.001},
    "websiteUri": "https://joescoffee.example",
    "nationalPhoneNumber": "+1 555-1234",
    "primaryType": "cafe",
    "types": ["cafe", "food"],
    "rating": 4.5,
    "userRatingCount": 120,
    "businessStatus": "OPERATIONAL",
}

PLACE_NO_SITE = {
    "id": "def456",
    "displayName": {"text": "No Website LLC"},
    "formattedAddress": "456 Side St",
    "location": {"latitude": 37.002, "longitude": -122.002},
    "primaryType": "store",
    "types": ["store"],
    "businessStatus": "OPERATIONAL",
}


def make_storage(tmp_path):
    return Storage(str(tmp_path / "test.db"))


def test_upsert_reports_new_vs_existing(tmp_path):
    s = make_storage(tmp_path)
    assert s.upsert_place(PLACE_WITH_SITE, ring=0) is True
    assert s.upsert_place(PLACE_WITH_SITE, ring=0) is False
    s.close()


def test_counts_and_website_filter(tmp_path):
    s = make_storage(tmp_path)
    s.upsert_place(PLACE_WITH_SITE, ring=0)
    s.upsert_place(PLACE_NO_SITE, ring=1)
    s.commit()

    assert s.count_places() == 2
    assert s.count_places(with_website_only=True) == 1
    s.close()


def test_tile_visited_tracking(tmp_path):
    s = make_storage(tmp_path)
    tile = Tile(ring=0, lat=37.0, lng=-122.0, radius_m=500)

    assert s.is_tile_visited(tile) is False
    s.mark_tile_visited(tile, result_count=2)
    s.commit()
    assert s.is_tile_visited(tile) is True
    s.close()


def test_csv_export_filters_by_website(tmp_path):
    s = make_storage(tmp_path)
    s.upsert_place(PLACE_WITH_SITE, ring=0)
    s.upsert_place(PLACE_NO_SITE, ring=1)
    s.commit()

    all_csv = tmp_path / "all.csv"
    website_csv = tmp_path / "website_only.csv"
    s.export_csv(str(all_csv))
    s.export_csv(str(website_csv), with_website_only=True)
    s.close()

    all_lines = all_csv.read_text().strip().splitlines()
    website_lines = website_csv.read_text().strip().splitlines()

    assert len(all_lines) == 3  # header + 2 rows
    assert len(website_lines) == 2  # header + 1 row
    assert "joescoffee.example" in website_lines[1]
