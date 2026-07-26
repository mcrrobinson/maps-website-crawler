"""SQLite persistence for Lighthouse (PageSpeed Insights) audit results."""

import csv
import sqlite3
from contextlib import closing
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS lighthouse_audits (
    website TEXT NOT NULL,
    strategy TEXT NOT NULL,
    final_url TEXT,
    performance_score INTEGER,
    accessibility_score INTEGER,
    best_practices_score INTEGER,
    seo_score INTEGER,
    first_contentful_paint_ms REAL,
    largest_contentful_paint_ms REAL,
    speed_index_ms REAL,
    total_blocking_time_ms REAL,
    cumulative_layout_shift REAL,
    time_to_interactive_ms REAL,
    screenshot_path TEXT,
    error TEXT,
    audited_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (website, strategy)
);
"""

COLUMNS = [
    "website", "strategy", "final_url", "performance_score", "accessibility_score",
    "best_practices_score", "seo_score", "first_contentful_paint_ms",
    "largest_contentful_paint_ms", "speed_index_ms", "total_blocking_time_ms",
    "cumulative_layout_shift", "time_to_interactive_ms", "screenshot_path",
    "error", "audited_at",
]


class LighthouseStorage:
    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def has_audit(self, website: str, strategy: str) -> bool:
        """True if a *successful* audit is already on file (failed audits
        are retried on the next run unless skipped explicitly)."""
        cur = self.conn.execute(
            "SELECT 1 FROM lighthouse_audits WHERE website = ? AND strategy = ? AND error IS NULL",
            (website, strategy),
        )
        return cur.fetchone() is not None

    def upsert_audit(
        self,
        website: str,
        strategy: str,
        parsed: Optional[dict],
        screenshot_path: Optional[str],
        error: Optional[str],
    ):
        parsed = parsed or {}
        self.conn.execute(
            """INSERT INTO lighthouse_audits
                (website, strategy, final_url, performance_score, accessibility_score,
                 best_practices_score, seo_score, first_contentful_paint_ms,
                 largest_contentful_paint_ms, speed_index_ms, total_blocking_time_ms,
                 cumulative_layout_shift, time_to_interactive_ms, screenshot_path, error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(website, strategy) DO UPDATE SET
                final_url=excluded.final_url,
                performance_score=excluded.performance_score,
                accessibility_score=excluded.accessibility_score,
                best_practices_score=excluded.best_practices_score,
                seo_score=excluded.seo_score,
                first_contentful_paint_ms=excluded.first_contentful_paint_ms,
                largest_contentful_paint_ms=excluded.largest_contentful_paint_ms,
                speed_index_ms=excluded.speed_index_ms,
                total_blocking_time_ms=excluded.total_blocking_time_ms,
                cumulative_layout_shift=excluded.cumulative_layout_shift,
                time_to_interactive_ms=excluded.time_to_interactive_ms,
                screenshot_path=excluded.screenshot_path,
                error=excluded.error,
                audited_at=CURRENT_TIMESTAMP
            """,
            (
                website, strategy, parsed.get("final_url"),
                parsed.get("performance_score"), parsed.get("accessibility_score"),
                parsed.get("best_practices_score"), parsed.get("seo_score"),
                parsed.get("first_contentful_paint_ms"), parsed.get("largest_contentful_paint_ms"),
                parsed.get("speed_index_ms"), parsed.get("total_blocking_time_ms"),
                parsed.get("cumulative_layout_shift"), parsed.get("time_to_interactive_ms"),
                screenshot_path, error,
            ),
        )

    def commit(self):
        self.conn.commit()

    def count_audits(self, errors_only: bool = False) -> int:
        query = "SELECT COUNT(*) FROM lighthouse_audits"
        if errors_only:
            query += " WHERE error IS NOT NULL"
        return self.conn.execute(query).fetchone()[0]

    def export_csv(self, path: str):
        query = f"SELECT {', '.join(COLUMNS)} FROM lighthouse_audits ORDER BY website, strategy"
        with closing(self.conn.execute(query)) as cur, open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(COLUMNS)
            writer.writerows(cur)

    def close(self):
        self.conn.close()
