"""Stable resource locks for conflicting PhotoHome mutations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class LockMode(StrEnum):
    EXCLUSIVE = "exclusive"


@dataclass(frozen=True, order=True)
class ResourceLockRequest:
    resource_type: str
    resource_id: str
    owner_item_id: str | None = None
    lock_mode: LockMode = LockMode.EXCLUSIVE

    def __post_init__(self) -> None:
        if not self.resource_type or not self.resource_id:
            raise ValueError("A stable resource type and identifier are required")


@dataclass(frozen=True)
class ResourceLock:
    resource_type: str
    resource_id: str
    lock_mode: LockMode
    owner_operation_id: str
    owner_item_id: str | None
    token: str
    acquired_at: str
    heartbeat_at: str
    expires_at: str


@dataclass(frozen=True)
class LockConflict:
    resource_type: str
    resource_id: str
    owner_operation_id: str
    next_action: str


@dataclass(frozen=True)
class LockAcquireResult:
    locks: tuple[ResourceLock, ...]
    conflicts: tuple[LockConflict, ...] = ()

    @property
    def acquired(self) -> bool:
        return not self.conflicts
