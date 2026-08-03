"""Isolated factories for integration tests of the new operation core."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from app.application.services.operation_manager import OperationManager
from app.db import Database
from app.infrastructure.database.locks import SqliteResourceLockRepository
from app.infrastructure.database.operations import SqliteOperationRepository
from app.infrastructure.diagnostics import DiagnosticService
from app.infrastructure.filesystem.executor import LocalFileOperationExecutor


@dataclass
class CoreEnvironment:
    root: Path
    photos: Path
    quarantine: Path
    database: Database
    manager: OperationManager
    locks: SqliteResourceLockRepository
    executor: LocalFileOperationExecutor
    diagnostics: DiagnosticService
    huey_database: Path


@pytest.fixture
def core_environment(tmp_path: Path) -> CoreEnvironment:
    photos, quarantine = tmp_path / "photos", tmp_path / ".quarantine"
    photos.mkdir()
    quarantine.mkdir()
    database = Database(tmp_path / "data" / "photo-review.sqlite3")
    database.initialize()
    locks = SqliteResourceLockRepository(database)
    manager = OperationManager(SqliteOperationRepository(database), locks)
    return CoreEnvironment(tmp_path, photos, quarantine, database, manager, locks, LocalFileOperationExecutor(database=database, roots={"photos": photos, "quarantine": quarantine}, lock_repository=locks), DiagnosticService(database), tmp_path / "queue" / "huey.sqlite3")
