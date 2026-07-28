"""SQLite persistence for site-quality audits: one row per site plus one
row per (site, source page, link) checked."""

import csv
import json
import sqlite3
from contextlib import closing

SCHEMA = """
CREATE TABLE IF NOT EXISTS site_audits (
    website TEXT PRIMARY KEY,
    final_url TEXT,
    http_status INTEGER,
    pages_crawled INTEGER,
    load_time_ms REAL,
    desktop_look_score INTEGER,
    desktop_look_summary TEXT,
    desktop_issues TEXT,
    mobile_look_score INTEGER,
    mobile_look_summary TEXT,
    mobile_issues TEXT,
    desktop_screenshot_path TEXT,
    mobile_screenshot_path TEXT,
    broken_link_count INTEGER,
    total_link_count INTEGER,
    error TEXT,
    audited_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS site_links (
    website TEXT NOT NULL,
    source_page TEXT NOT NULL,
    link_url TEXT NOT NULL,
    status_code INTEGER,
    classification TEXT,
    error TEXT,
    checked_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (website, source_page, link_url)
);
"""

AUDIT_COLUMNS = [
    "website", "final_url", "http_status", "pages_crawled", "load_time_ms",
    "desktop_look_score", "desktop_look_summary", "desktop_issues",
    "mobile_look_score", "mobile_look_summary", "mobile_issues",
    "desktop_screenshot_path", "mobile_screenshot_path",
    "broken_link_count", "total_link_count", "error", "audited_at",
]

LINK_COLUMNS = [
    "website", "source_page", "link_url", "status_code", "classification",
    "error", "checked_at",
]


class SiteAuditStorage:
    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def has_audit(self, website: str) -> bool:
        """True if a *successful* audit is already on file (failed audits
        are retried on the next run unless skipped explicitly)."""
        cur = self.conn.execute(
            "SELECT 1 FROM site_audits WHERE website = ? AND error IS NULL", (website,)
        )
        return cur.fetchone() is not None

    def upsert_audit(self, result: dict):
        broken = sum(1 for r in result.get("link_results", []) if r.classification == "broken")
        total = len(result.get("link_results", []))

        desktop_review = result.get("desktop_review")
        mobile_review = result.get("mobile_review")

        def summary_text(review):
            if review is None:
                return None
            if review.error:
                return f"[vision scoring failed: {review.error}]"
            return review.summary

        self.conn.execute(
            """INSERT INTO site_audits
                (website, final_url, http_status, pages_crawled, load_time_ms,
                 desktop_look_score, desktop_look_summary, desktop_issues,
                 mobile_look_score, mobile_look_summary, mobile_issues,
                 desktop_screenshot_path, mobile_screenshot_path,
                 broken_link_count, total_link_count, error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(website) DO UPDATE SET
                final_url=excluded.final_url,
                http_status=excluded.http_status,
                pages_crawled=excluded.pages_crawled,
                load_time_ms=excluded.load_time_ms,
                desktop_look_score=excluded.desktop_look_score,
                desktop_look_summary=excluded.desktop_look_summary,
                desktop_issues=excluded.desktop_issues,
                mobile_look_score=excluded.mobile_look_score,
                mobile_look_summary=excluded.mobile_look_summary,
                mobile_issues=excluded.mobile_issues,
                desktop_screenshot_path=excluded.desktop_screenshot_path,
                mobile_screenshot_path=excluded.mobile_screenshot_path,
                broken_link_count=excluded.broken_link_count,
                total_link_count=excluded.total_link_count,
                error=excluded.error,
                audited_at=CURRENT_TIMESTAMP
            """,
            (
                result["website"], result.get("final_url"), result.get("http_status"),
                result.get("pages_crawled"), result.get("load_time_ms"),
                desktop_review.look_score if desktop_review else None,
                summary_text(desktop_review),
                json.dumps(desktop_review.issues) if desktop_review else None,
                mobile_review.look_score if mobile_review else None,
                summary_text(mobile_review),
                json.dumps(mobile_review.issues) if mobile_review else None,
                result.get("desktop_screenshot_path"), result.get("mobile_screenshot_path"),
                broken, total, result.get("error"),
            ),
        )

        for link_result in result.get("link_results", []):
            self.conn.execute(
                """INSERT INTO site_links
                    (website, source_page, link_url, status_code, classification, error)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(website, source_page, link_url) DO UPDATE SET
                    status_code=excluded.status_code,
                    classification=excluded.classification,
                    error=excluded.error,
                    checked_at=CURRENT_TIMESTAMP
                """,
                (
                    result["website"], link_result.source_page, link_result.url,
                    link_result.status_code, link_result.classification, link_result.error,
                ),
            )

    def commit(self):
        self.conn.commit()

    def count_audits(self, errors_only: bool = False) -> int:
        query = "SELECT COUNT(*) FROM site_audits"
        if errors_only:
            query += " WHERE error IS NOT NULL"
        return self.conn.execute(query).fetchone()[0]

    def export_csv(self, path: str):
        query = f"SELECT {', '.join(AUDIT_COLUMNS)} FROM site_audits ORDER BY website"
        with closing(self.conn.execute(query)) as cur, open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(AUDIT_COLUMNS)
            writer.writerows(cur)

    def export_links_csv(self, path: str):
        query = f"SELECT {', '.join(LINK_COLUMNS)} FROM site_links ORDER BY website, source_page, link_url"
        with closing(self.conn.execute(query)) as cur, open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(LINK_COLUMNS)
            writer.writerows(cur)

    def close(self):
        self.conn.close()
