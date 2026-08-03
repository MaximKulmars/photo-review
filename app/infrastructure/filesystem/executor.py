"""Verified, idempotent file changes isolated from web and worker handlers."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import asdict
from pathlib import Path, PurePosixPath
from typing import Callable

from app.application.commands.file_operations import (
    FileCommandType,
    FileOperationCommand,
    FileOperationResult,
)
from app.db import Database


class FileOperationError(RuntimeError):
    code = "file_operation_failed"


class PathValidationError(FileOperationError):
    code = "invalid_path"


class DestinationConflictError(FileOperationError):
    code = "destination_conflict"


class StaleSourceVersionError(FileOperationError):
    code = "stale_source_version"


class UnknownOutcomeError(FileOperationError):
    code = "needs_reconciliation"


class LocalFilesystem:
    """Small adapter around platform operations used by the executor only."""

    @staticmethod
    def same_filesystem(source: Path, destination_parent: Path) -> bool:
        return source.stat().st_dev == destination_parent.stat().st_dev

    @staticmethod
    def fsync_file(path: Path) -> None:
        with path.open("rb") as stream:
            os.fsync(stream.fileno())

    @staticmethod
    def fsync_directory(path: Path) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


DatabaseUpdate = Callable[[object], None]


class LocalFileOperationExecutor:
    """Apply one checked filesystem change and persist its confirmed outcome.

    It deliberately owns no Operation Manager lifecycle, scheduling, or HTTP concern.
    """

    def __init__(
        self,
        *,
        database: Database,
        roots: dict[str, Path],
        filesystem: LocalFilesystem | None = None,
        database_update: DatabaseUpdate | None = None,
    ):
        self.database = database
        self.roots = {name: path.resolve() for name, path in roots.items()}
        self.filesystem = filesystem or LocalFilesystem()
        self.database_update = database_update

    def execute(self, command: FileOperationCommand) -> FileOperationResult:
        fingerprint = self._fingerprint(command)
        previous = self._load_command(command.command_id)
        if previous:
            if previous["fingerprint"] != fingerprint:
                raise FileOperationError("command_id cannot be reused with different parameters")
            if previous["status"] == "succeeded":
                return FileOperationResult(**json.loads(previous["result_json"]))
            raise UnknownOutcomeError("previous result needs reconciliation before retry")

        self._record_started(command, fingerprint)
        try:
            result, compensation = self._apply(command)
        except Exception as exc:
            self._record_failed(command.command_id, getattr(exc, "code", "file_operation_failed"))
            raise

        try:
            with self.database.connect() as connection:
                if self.database_update:
                    self.database_update(connection)
                connection.execute(
                    "UPDATE file_execution_commands SET status='succeeded', result_json=?, updated_at=CURRENT_TIMESTAMP WHERE command_id=?",
                    (json.dumps(asdict(result), sort_keys=True), command.command_id),
                )
        except Exception:
            if compensation and compensation():
                self._record_failed(command.command_id, "database_update_failed_compensated")
                raise
            self._record_reconciliation(command.command_id, result)
            raise UnknownOutcomeError("filesystem result is confirmed; database needs reconciliation")
        return result

    def _apply(self, command: FileOperationCommand) -> tuple[FileOperationResult, Callable[[], bool] | None]:
        destination = self._destination(command.destination_root, command.destination_path)
        if command.command_type is FileCommandType.MKDIR:
            destination.mkdir(parents=True, exist_ok=False)
            self.filesystem.fsync_directory(destination.parent)
            return self._result(command, "directory"), lambda: self._remove_empty(destination)

        source = self._source(command)
        self._verify_version(source, command.expected_source_version)
        if command.command_type is FileCommandType.SYMLINK:
            return self._symlink(command, source, destination)
        if destination.exists() or destination.is_symlink():
            raise DestinationConflictError("destination already exists")
        destination.parent.mkdir(parents=True, exist_ok=True)

        if command.command_type in {FileCommandType.MOVE, FileCommandType.QUARANTINE, FileCommandType.RESTORE} and self.filesystem.same_filesystem(source, destination.parent):
            os.replace(source, destination)
            self.filesystem.fsync_directory(destination.parent)
            self._verify_move(source, destination)
            # A confirmed move may be the only surviving copy. Its DB failure must
            # be reconciled, never "compensated" by another unverified move.
            return self._result(command, "atomic_rename"), None

        copied_hash = self._copy_via_temp(source, destination, command.operation_id)
        if command.command_type in {FileCommandType.MOVE, FileCommandType.QUARANTINE, FileCommandType.RESTORE}:
            try:
                source.unlink()
            except OSError as exc:
                raise UnknownOutcomeError("copy succeeded but source removal was not confirmed") from exc
            self._verify_move(source, destination)
            return self._result(command, f"sha256:{copied_hash}"), None
        self._verify_copy(source, destination, copied_hash)
        return self._result(command, f"sha256:{copied_hash}"), lambda: self._unlink(destination)

    def _symlink(self, command: FileOperationCommand, source: Path, destination: Path):
        if destination.exists() or destination.is_symlink():
            raise DestinationConflictError("destination already exists")
        destination.parent.mkdir(parents=True, exist_ok=True)
        relative_target = os.path.relpath(source, destination.parent)
        try:
            os.symlink(relative_target, destination)
        except OSError as exc:
            raise FileOperationError("relative symlink could not be created") from exc
        if not destination.is_symlink() or destination.resolve() != source.resolve():
            self._unlink(destination)
            raise FileOperationError("relative symlink verification failed")
        return self._result(command, "relative_symlink"), lambda: self._unlink(destination)

    def _copy_via_temp(self, source: Path, destination: Path, operation_id: str) -> str:
        prefix = f".{destination.name}.photohome-{operation_id}-"
        descriptor, temp_name = tempfile.mkstemp(prefix=prefix, suffix=".tmp", dir=destination.parent)
        temporary = Path(temp_name)
        try:
            with os.fdopen(descriptor, "wb") as target, source.open("rb") as origin:
                shutil.copyfileobj(origin, target, length=1024 * 1024)
                target.flush()
                os.fsync(target.fileno())
            expected_hash = self._sha256(source)
            copied_hash = self._sha256(temporary)
            if copied_hash != expected_hash or temporary.stat().st_size != source.stat().st_size:
                raise FileOperationError("copied file verification failed")
            os.replace(temporary, destination)
            self.filesystem.fsync_directory(destination.parent)
            return copied_hash
        finally:
            if temporary.exists() or temporary.is_symlink():
                temporary.unlink()

    def _source(self, command: FileOperationCommand) -> Path:
        if not command.source_root or command.source_path is None:
            raise PathValidationError("source is required")
        source = self._path(command.source_root, command.source_path)
        if not source.is_file() or source.is_symlink():
            raise PathValidationError("source must be an ordinary file inside an allowed root")
        return source

    def _destination(self, root: str, relative: str) -> Path:
        return self._path(root, relative, allow_missing=True)

    def _path(self, root_name: str, relative: str, *, allow_missing: bool = False) -> Path:
        root = self.roots.get(root_name)
        if root is None:
            raise PathValidationError("unknown filesystem root")
        relative_path = PurePosixPath(relative)
        if not relative or relative_path.is_absolute() or ".." in relative_path.parts or "\x00" in relative:
            raise PathValidationError("path must be a non-empty relative path")
        candidate = root.joinpath(*relative_path.parts)
        current = root
        for part in relative_path.parts[:-1] if allow_missing else relative_path.parts:
            current = current / part
            if current.exists() and current.is_symlink():
                raise PathValidationError("path crosses a symbolic link")
        try:
            candidate.resolve(strict=False).relative_to(root)
        except ValueError as exc:
            raise PathValidationError("path escapes its allowed root") from exc
        return candidate

    def _verify_version(self, source: Path, expected: str | None) -> None:
        if expected and self._sha256(source) != expected:
            raise StaleSourceVersionError("source content no longer matches the expected version")

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _verify_move(source: Path, destination: Path) -> None:
        if source.exists() or not destination.is_file() or destination.is_symlink():
            raise UnknownOutcomeError("move result could not be verified")

    @staticmethod
    def _verify_copy(source: Path, destination: Path, expected_hash: str) -> None:
        if not source.is_file() or not destination.is_file() or destination.is_symlink():
            raise UnknownOutcomeError("copy result could not be verified")
        if source.stat().st_size != destination.stat().st_size or LocalFileOperationExecutor._sha256(destination) != expected_hash:
            raise UnknownOutcomeError("copy content could not be verified")

    def _result(self, command: FileOperationCommand, verification: str) -> FileOperationResult:
        return FileOperationResult(command.command_id, command.operation_id, "succeeded", command.source_root, command.source_path, command.destination_root, command.destination_path, verification)

    @staticmethod
    def _unlink(path: Path) -> bool:
        if path.exists() or path.is_symlink():
            path.unlink()
        return True

    @staticmethod
    def _remove_empty(path: Path) -> bool:
        try:
            path.rmdir()
        except OSError:
            return False
        return True

    @staticmethod
    def _fingerprint(command: FileOperationCommand) -> str:
        return hashlib.sha256(json.dumps(asdict(command), sort_keys=True, default=str).encode()).hexdigest()

    def _load_command(self, command_id: str):
        return self.database.one("SELECT * FROM file_execution_commands WHERE command_id=?", (command_id,))

    def _record_started(self, command: FileOperationCommand, fingerprint: str) -> None:
        with self.database.connect() as connection:
            connection.execute("INSERT INTO file_execution_commands(command_id, operation_id, operation_item_id, idempotency_key, fingerprint, status) VALUES (?, ?, ?, ?, ?, 'running')", (command.command_id, command.operation_id, command.operation_item_id, command.idempotency_key or command.command_id, fingerprint))

    def _record_failed(self, command_id: str, error_code: str) -> None:
        self.database.execute("UPDATE file_execution_commands SET status='failed', error_code=?, updated_at=CURRENT_TIMESTAMP WHERE command_id=?", (error_code, command_id))

    def _record_reconciliation(self, command_id: str, result: FileOperationResult) -> None:
        self.database.execute("UPDATE file_execution_commands SET status='needs_reconciliation', result_json=?, error_code='database_update_failed', updated_at=CURRENT_TIMESTAMP WHERE command_id=?", (json.dumps(asdict(result), sort_keys=True), command_id))
