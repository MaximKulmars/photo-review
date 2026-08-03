"""Centralized SqliteHuey configuration."""

from __future__ import annotations

from huey import SqliteHuey

from ...config import Config, load_config


def create_huey(config: Config) -> SqliteHuey:
    queue_path = config.huey_db_path or config.data_root / "queue" / "huey.sqlite3"
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    return SqliteHuey("photohome", filename=str(queue_path), immediate=config.huey_immediate, results=True, utc=True)
