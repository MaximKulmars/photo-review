from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS media (
    id INTEGER PRIMARY KEY,
    relative_path TEXT NOT NULL UNIQUE,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    width INTEGER,
    height INTEGER,
    captured_at TEXT,
    sha256 TEXT,
    phash TEXT,
    brightness REAL,
    sharpness REAL,
    edge_density REAL,
    text_length INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    quarantine_path TEXT,
    analysis_revision INTEGER NOT NULL DEFAULT 0,
    last_scan_job_id INTEGER,
    manual_quality INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS media_hash_idx ON media(sha256);
CREATE INDEX IF NOT EXISTS media_status_idx ON media(status);
CREATE INDEX IF NOT EXISTS media_phash_idx ON media(phash);

CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY,
    media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    reason TEXT NOT NULL,
    score REAL NOT NULL DEFAULT 0,
    group_key TEXT,
    suggested_best INTEGER NOT NULL DEFAULT 0,
    decision TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(media_id, category, group_key)
);
CREATE INDEX IF NOT EXISTS findings_queue_idx
    ON findings(category, decision);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    scope TEXT NOT NULL,
    duplicate_scope TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'queued',
    total INTEGER NOT NULL DEFAULT 0,
    processed INTEGER NOT NULL DEFAULT 0,
    skipped INTEGER NOT NULL DEFAULT 0,
    unsupported INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0,
    message TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TEXT,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY,
    action TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    details TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


DEFAULT_SETTINGS: dict[str, Any] = {
    "analysis_revision": 1,
    "blur_threshold": 55.0,
    "dark_threshold": 28.0,
    "similar_distance": 7,
    "ocr_min_chars": 45,
    "sensitivity": "careful",
}


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._local = threading.local()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(media)")}
            if "last_scan_job_id" not in columns:
                connection.execute("ALTER TABLE media ADD COLUMN last_scan_job_id INTEGER")
            if "manual_quality" not in columns:
                connection.execute(
                    "ALTER TABLE media ADD COLUMN manual_quality INTEGER NOT NULL DEFAULT 0"
                )
            for key, value in DEFAULT_SETTINGS.items():
                connection.execute(
                    "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
                    (key, json.dumps(value, ensure_ascii=False)),
                )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def one(self, sql: str, parameters: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(sql, parameters).fetchone()

    def all(
        self, sql: str, parameters: tuple[Any, ...] = ()
    ) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(sql, parameters).fetchall()

    def execute(self, sql: str, parameters: tuple[Any, ...] = ()) -> int:
        with self.connect() as connection:
            cursor = connection.execute(sql, parameters)
            return int(cursor.lastrowid or 0)

    def settings(self) -> dict[str, Any]:
        rows = self.all("SELECT key, value FROM settings")
        return {row["key"]: json.loads(row["value"]) for row in rows}

    def save_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "blur_threshold",
            "dark_threshold",
            "similar_distance",
            "ocr_min_chars",
            "sensitivity",
        }
        with self.connect() as connection:
            for key, value in values.items():
                if key not in allowed:
                    continue
                connection.execute(
                    """
                    INSERT INTO settings(key, value) VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value
                    """,
                    (key, json.dumps(value, ensure_ascii=False)),
                )
            row = connection.execute(
                "SELECT value FROM settings WHERE key='analysis_revision'"
            ).fetchone()
            revision = json.loads(row["value"]) + 1
            connection.execute(
                "UPDATE settings SET value=? WHERE key='analysis_revision'",
                (json.dumps(revision),),
            )
        return self.settings()
