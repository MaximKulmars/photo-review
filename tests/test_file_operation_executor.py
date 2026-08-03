from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest

from app.application.commands.file_operations import FileCommandType, FileOperationCommand
from app.db import Database
from app.infrastructure.filesystem.executor import (
    DestinationConflictError,
    FileOperationError,
    LocalFileOperationExecutor,
    LocalFilesystem,
    PathValidationError,
    StaleSourceVersionError,
    UnknownOutcomeError,
)


class CrossFilesystem(LocalFilesystem):
    @staticmethod
    def same_filesystem(source: Path, destination_parent: Path) -> bool:
        return False


@pytest.fixture
def executor(tmp_path: Path):
    photos = tmp_path / "photos"
    quarantine = tmp_path / ".quarantine"
    photos.mkdir()
    quarantine.mkdir()
    database = Database(tmp_path / "data" / "app.sqlite3")
    database.initialize()
    return LocalFileOperationExecutor(database=database, roots={"photos": photos, "quarantine": quarantine}), photos, quarantine, database


def command(kind: FileCommandType, *, source_root="photos", source_path="source.jpg", destination_root="photos", destination_path="target.jpg", command_id="command-1", version=None):
    return FileOperationCommand(command_id=command_id, operation_id="operation-1", operation_item_id="item-1", command_type=kind, source_root=source_root, source_path=source_path, destination_root=destination_root, destination_path=destination_path, expected_source_version=version, idempotency_key=command_id)


def test_atomic_rename_and_idempotent_replay(executor):
    service, photos, _, database = executor
    (photos / "source.jpg").write_bytes(b"photo")
    result = service.execute(command(FileCommandType.MOVE))
    replay = service.execute(command(FileCommandType.MOVE))
    assert result.verification == "atomic_rename"
    assert replay == result
    assert not (photos / "source.jpg").exists()
    assert (photos / "target.jpg").read_bytes() == b"photo"
    assert database.one("SELECT status FROM file_execution_commands WHERE command_id='command-1'")["status"] == "succeeded"


def test_cross_filesystem_copy_uses_verified_temp_and_preserves_source(executor, tmp_path):
    _, photos, quarantine, database = executor
    (photos / "source.jpg").write_bytes(b"photo")
    service = LocalFileOperationExecutor(database=database, roots={"photos": photos, "quarantine": quarantine}, filesystem=CrossFilesystem())
    result = service.execute(command(FileCommandType.COPY))
    assert result.verification == "sha256:" + hashlib.sha256(b"photo").hexdigest()
    assert (photos / "source.jpg").exists()
    assert (photos / "target.jpg").read_bytes() == b"photo"
    assert not list(photos.glob("*.tmp"))


def test_cross_filesystem_move_only_deletes_source_after_copy_verifies(executor):
    _, photos, quarantine, database = executor
    (photos / "source.jpg").write_bytes(b"photo")
    service = LocalFileOperationExecutor(database=database, roots={"photos": photos, "quarantine": quarantine}, filesystem=CrossFilesystem())
    service.execute(command(FileCommandType.MOVE))
    assert not (photos / "source.jpg").exists()
    assert (photos / "target.jpg").read_bytes() == b"photo"


def test_copy_write_failure_cleans_temp_and_preserves_source(executor, monkeypatch):
    _, photos, quarantine, database = executor
    (photos / "source.jpg").write_bytes(b"photo")
    service = LocalFileOperationExecutor(database=database, roots={"photos": photos, "quarantine": quarantine}, filesystem=CrossFilesystem())
    def fail_copy(origin, target, length):
        target.write(b"partial")
        raise OSError("disk full")
    monkeypatch.setattr(shutil, "copyfileobj", fail_copy)
    with pytest.raises(OSError):
        service.execute(command(FileCommandType.COPY))
    assert (photos / "source.jpg").read_bytes() == b"photo"
    assert not (photos / "target.jpg").exists()
    assert not list(photos.glob("*.tmp"))


def test_destination_conflict_and_stale_version_are_rejected(executor):
    service, photos, _, _ = executor
    (photos / "source.jpg").write_bytes(b"photo")
    (photos / "target.jpg").write_bytes(b"other")
    with pytest.raises(DestinationConflictError):
        service.execute(command(FileCommandType.COPY))
    with pytest.raises(StaleSourceVersionError):
        service.execute(command(FileCommandType.COPY, command_id="command-2", destination_path="other.jpg", version="not-a-hash"))


def test_traversal_and_symlink_escape_are_rejected(executor, tmp_path):
    service, photos, _, _ = executor
    (photos / "source.jpg").write_bytes(b"photo")
    with pytest.raises(PathValidationError):
        service.execute(command(FileCommandType.COPY, destination_path="../outside.jpg"))
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (photos / "linked").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("creating symlinks requires Windows developer privileges")
    with pytest.raises(PathValidationError):
        service.execute(command(FileCommandType.COPY, command_id="command-2", destination_path="linked/outside.jpg"))


def test_quarantine_restore_and_relative_symlink(executor):
    service, photos, quarantine, _ = executor
    (photos / "album").mkdir()
    (photos / "album" / "source.jpg").write_bytes(b"photo")
    service.execute(command(FileCommandType.QUARANTINE, source_path="album/source.jpg", destination_root="quarantine", destination_path="album/source.jpg"))
    assert (quarantine / "album" / "source.jpg").exists()
    service.execute(command(FileCommandType.RESTORE, command_id="command-2", source_root="quarantine", source_path="album/source.jpg", destination_path="restored.jpg"))
    link = command(FileCommandType.SYMLINK, command_id="command-3", source_path="restored.jpg", destination_path="album/favorite.jpg")
    try:
        service.execute(link)
    except FileOperationError:
        pytest.skip("creating symlinks requires Windows developer privileges")
    assert (photos / "album" / "favorite.jpg").is_symlink()
    assert (photos / "album" / "favorite.jpg").readlink().is_absolute() is False
    assert (photos / "album" / "favorite.jpg").resolve() == (photos / "restored.jpg").resolve()


def test_database_failure_after_copy_compensates_and_marks_failure(executor):
    _, photos, quarantine, database = executor
    (photos / "source.jpg").write_bytes(b"photo")
    def fail_update(connection):
        raise RuntimeError("database unavailable")
    service = LocalFileOperationExecutor(database=database, roots={"photos": photos, "quarantine": quarantine}, filesystem=CrossFilesystem(), database_update=fail_update)
    with pytest.raises(RuntimeError):
        service.execute(command(FileCommandType.COPY))
    assert (photos / "source.jpg").exists()
    assert not (photos / "target.jpg").exists()
    assert database.one("SELECT status FROM file_execution_commands WHERE command_id='command-1'")["status"] == "failed"


def test_database_failure_after_move_requires_reconciliation(executor):
    _, photos, quarantine, database = executor
    (photos / "source.jpg").write_bytes(b"photo")
    def fail_update(connection):
        raise RuntimeError("database unavailable")
    service = LocalFileOperationExecutor(database=database, roots={"photos": photos, "quarantine": quarantine}, database_update=fail_update)
    with pytest.raises(UnknownOutcomeError):
        service.execute(command(FileCommandType.MOVE))
    assert not (photos / "source.jpg").exists()
    assert (photos / "target.jpg").exists()
    assert database.one("SELECT status FROM file_execution_commands WHERE command_id='command-1'")["status"] == "needs_reconciliation"
