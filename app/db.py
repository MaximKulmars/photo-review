from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .migrations import (
    LATEST_SCHEMA_VERSION,
    detect_schema_version,
    migrate,
    rollback,
)


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
        self.last_migration_backup: Path | None = None

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")
            current_version = detect_schema_version(connection)
            if current_version < LATEST_SCHEMA_VERSION:
                if current_version:
                    self.last_migration_backup = self.create_backup(
                        f"pre-migration-v{current_version}-to-v{LATEST_SCHEMA_VERSION}"
                    )
                migrate(connection, current_version)
            for key, value in DEFAULT_SETTINGS.items():
                connection.execute(
                    "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
                    (key, json.dumps(value, ensure_ascii=False)),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def schema_version(self) -> int:
        with self.connect() as connection:
            return detect_schema_version(connection)

    def create_backup(self, reason: str = "manual") -> Path:
        if not self.path.is_file():
            raise FileNotFoundError("База данных ещё не создана")
        safe_reason = "".join(
            character if character.isalnum() or character in {"-", "_"} else "-"
            for character in reason
        ).strip("-") or "manual"
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        destination = (
            self.path.parent
            / "backups"
            / f"{self.path.stem}-{safe_reason}-{timestamp}{self.path.suffix}"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        source = sqlite3.connect(self.path, timeout=30)
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
            integrity = target.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise sqlite3.DatabaseError(
                    f"Проверка резервной копии завершилась ошибкой: {integrity}"
                )
        finally:
            target.close()
            source.close()
        return destination

    def rollback_schema(
        self, target_version: int, *, backup: bool = True
    ) -> Path | None:
        current_version = self.schema_version()
        backup_path = (
            self.create_backup(
                f"pre-rollback-v{current_version}-to-v{target_version}"
            )
            if backup
            else None
        )
        connection = sqlite3.connect(self.path, timeout=30)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            rollback(connection, current_version, target_version)
        finally:
            connection.close()
        return backup_path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
        except Exception:
            connection.rollback()
            raise
        else:
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
