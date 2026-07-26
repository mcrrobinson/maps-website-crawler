"""SQLite persistence for discovered places and searched tiles.

Tracking visited tiles lets a crawl be safely re-run (e.g. after being
interrupted, or to extend max_rings) without re-paying for API calls on
ground already covered.
"""

import json
import sqlite3
from contextlib import closing

from .grid import Tile

SCHEMA = """
CREATE TABLE IF NOT EXISTS places (
    place_id TEXT PRIMARY KEY,
    name TEXT,
    address TEXT,
    lat REAL,
    lng REAL,
    website TEXT,
    phone TEXT,
    primary_type TEXT,
    types TEXT,
    rating REAL,
    rating_count INTEGER,
    business_status TEXT,
    first_seen_ring INTEGER,
    discovered_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS visited_tiles (
    tile_key TEXT PRIMARY KEY,
    ring INTEGER,
    lat REAL,
    lng REAL,
    radius_m REAL,
    result_count INTEGER,
    searched_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


def _tile_key(tile: Tile) -> str:
    return f"{round(tile.lat, 6)}:{round(tile.lng, 6)}:{round(tile.radius_m)}"


class Storage:
    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def is_tile_visited(self, tile: Tile) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM visited_tiles WHERE tile_key = ?", (_tile_key(tile),)
        )
        return cur.fetchone() is not None

    def mark_tile_visited(self, tile: Tile, result_count: int):
        self.conn.execute(
            """INSERT OR REPLACE INTO visited_tiles
               (tile_key, ring, lat, lng, radius_m, result_count)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (_tile_key(tile), tile.ring, tile.lat, tile.lng, tile.radius_m, result_count),
        )

    def upsert_place(self, place: dict, ring: int) -> bool:
        """Inserts a place from the Places API response. Returns True if new."""
        place_id = place.get("id")
        if not place_id:
            return False

        is_new = self.conn.execute(
            "SELECT 1 FROM places WHERE place_id = ?", (place_id,)
        ).fetchone() is None

        location = place.get("location", {})
        self.conn.execute(
            """INSERT INTO places
                (place_id, name, address, lat, lng, website, phone,
                 primary_type, types, rating, rating_count, business_status,
                 first_seen_ring)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(place_id) DO UPDATE SET
                name=excluded.name,
                address=excluded.address,
                lat=excluded.lat,
                lng=excluded.lng,
                website=excluded.website,
                phone=excluded.phone,
                primary_type=excluded.primary_type,
                types=excluded.types,
                rating=excluded.rating,
                rating_count=excluded.rating_count,
                business_status=excluded.business_status
            """,
            (
                place_id,
                place.get("displayName", {}).get("text"),
                place.get("formattedAddress"),
                location.get("latitude"),
                location.get("longitude"),
                place.get("websiteUri"),
                place.get("nationalPhoneNumber"),
                place.get("primaryType"),
                json.dumps(place.get("types", [])),
                place.get("rating"),
                place.get("userRatingCount"),
                place.get("businessStatus"),
                ring,
            ),
        )
        return is_new

    def commit(self):
        self.conn.commit()

    def count_places(self, with_website_only: bool = False) -> int:
        query = "SELECT COUNT(*) FROM places"
        if with_website_only:
            query += " WHERE website IS NOT NULL AND website != ''"
        return self.conn.execute(query).fetchone()[0]

    def export_csv(self, path: str, with_website_only: bool = False):
        import csv

        columns = [
            "place_id", "name", "address", "lat", "lng", "website", "phone",
            "primary_type", "types", "rating", "rating_count", "business_status",
            "first_seen_ring", "discovered_at",
        ]
        query = f"SELECT {', '.join(columns)} FROM places"
        if with_website_only:
            query += " WHERE website IS NOT NULL AND website != ''"
        query += " ORDER BY first_seen_ring, name"

        with closing(self.conn.execute(query)) as cur, open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(columns)
            writer.writerows(cur)

    def close(self):
        self.conn.close()
