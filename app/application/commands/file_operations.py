"""Immutable commands for the File Operation Executor."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class FileCommandType(StrEnum):
    MOVE = "move"
    COPY = "copy"
    QUARANTINE = "quarantine"
    RESTORE = "restore"
    MKDIR = "mkdir"
    SYMLINK = "symlink"


@dataclass(frozen=True)
class FileOperationCommand:
    command_id: str
    operation_id: str
    operation_item_id: str
    command_type: FileCommandType
    source_root: str | None = None
    source_path: str | None = None
    destination_root: str = "photos"
    destination_path: str = ""
    expected_source_version: str | None = None
    idempotency_key: str | None = None


@dataclass(frozen=True)
class FileOperationResult:
    command_id: str
    operation_id: str
    status: str
    source_root: str | None
    source_path: str | None
    destination_root: str
    destination_path: str
    verification: str
    needs_reconciliation: bool = False
