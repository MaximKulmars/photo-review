import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.migrations import LATEST_SCHEMA_VERSION


LEGACY_SCHEMA = """
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE media (
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
CREATE TABLE findings (
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
CREATE TABLE jobs (
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
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY,
    action TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    details TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class MigrationTests(unittest.TestCase):
    def test_fresh_database_reaches_latest_version(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "data.sqlite3")
            database.initialize()

            self.assertEqual(database.schema_version(), LATEST_SCHEMA_VERSION)
            history = database.all(
                "SELECT version, name FROM schema_migrations ORDER BY version"
            )
            self.assertEqual(
                [row["version"] for row in history],
                list(range(1, LATEST_SCHEMA_VERSION + 1)),
            )
            job_columns = {
                row["name"] for row in database.all("PRAGMA table_info(jobs)")
            }
            self.assertTrue({"kind", "payload"} <= job_columns)

    def test_legacy_database_is_backed_up_migrated_and_reversible(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.sqlite3"
            connection = sqlite3.connect(path)
            connection.executescript(LEGACY_SCHEMA)
            connection.execute(
                "INSERT INTO settings(key, value) VALUES('analysis_revision', ?)",
                (json.dumps(7),),
            )
            connection.execute(
                """
                INSERT INTO media(
                    relative_path, size, mtime_ns, sha256, manual_quality
                ) VALUES('2024/photo.jpg', 12, 34, 'abc', 1)
                """
            )
            connection.execute(
                """
                INSERT INTO jobs(scope, duplicate_scope, state, processed)
                VALUES('2024', 'scope', 'completed', 1)
                """
            )
            connection.commit()
            connection.close()

            database = Database(path)
            database.initialize()

            self.assertEqual(database.schema_version(), LATEST_SCHEMA_VERSION)
            self.assertIsNotNone(database.last_migration_backup)
            self.assertTrue(database.last_migration_backup.is_file())
            self.assertEqual(
                database.one("SELECT relative_path FROM media WHERE id=1")[
                    "relative_path"
                ],
                "2024/photo.jpg",
            )
            self.assertEqual(
                database.one("SELECT kind FROM jobs WHERE id=1")["kind"],
                "analysis",
            )
            backup = sqlite3.connect(database.last_migration_backup)
            try:
                backup_columns = {
                    row[1] for row in backup.execute("PRAGMA table_info(jobs)")
                }
                self.assertNotIn("kind", backup_columns)
                self.assertEqual(
                    backup.execute("SELECT COUNT(*) FROM media").fetchone()[0], 1
                )
            finally:
                backup.close()

            database.rollback_schema(3, backup=False)
            self.assertEqual(database.schema_version(), 3)
            self.assertEqual(
                database.one("SELECT COUNT(*) AS count FROM media")["count"], 1
            )
            database.initialize()
            self.assertEqual(database.schema_version(), LATEST_SCHEMA_VERSION)
            self.assertEqual(
                database.one("SELECT kind FROM jobs WHERE id=1")["kind"],
                "analysis",
            )

    def test_failed_connection_transaction_rolls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "data.sqlite3")
            database.initialize()

            with self.assertRaises(RuntimeError):
                with database.connect() as connection:
                    connection.execute(
                        "INSERT INTO settings(key, value) VALUES('temporary', '1')"
                    )
                    raise RuntimeError("stop")

            self.assertIsNone(
                database.one("SELECT value FROM settings WHERE key='temporary'")
            )


if __name__ == "__main__":
    unittest.main()
