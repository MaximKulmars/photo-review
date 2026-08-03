"""Framework-independent model of long-running PhotoHome operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import uuid4


class OperationStatus(StrEnum):
    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"
    PAUSING = "pausing"
    PAUSED = "paused"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    REQUIRES_ATTENTION = "requires_attention"


class OperationItemStatus(StrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"
    OBSOLETE = "obsolete"


class InvalidOperationTransition(ValueError):
    """Raised when an operation status transition is not part of its lifecycle."""


class InvalidOperationItemTransition(ValueError):
    """Raised when an item status transition is not part of its lifecycle."""


class IdempotencyConflictError(RuntimeError):
    """Raised when one idempotency key is reused for a different command."""


OPERATION_TRANSITIONS: dict[OperationStatus, frozenset[OperationStatus]] = {
    OperationStatus.CREATED: frozenset({OperationStatus.QUEUED, OperationStatus.FAILED, OperationStatus.CANCELLED}),
    OperationStatus.QUEUED: frozenset({OperationStatus.RUNNING, OperationStatus.FAILED, OperationStatus.CANCELLING, OperationStatus.CANCELLED, OperationStatus.INTERRUPTED}),
    OperationStatus.RUNNING: frozenset({OperationStatus.COMPLETED, OperationStatus.COMPLETED_WITH_ERRORS, OperationStatus.FAILED, OperationStatus.PAUSING, OperationStatus.CANCELLING, OperationStatus.INTERRUPTED, OperationStatus.REQUIRES_ATTENTION}),
    OperationStatus.PAUSING: frozenset({OperationStatus.PAUSED, OperationStatus.CANCELLING, OperationStatus.INTERRUPTED, OperationStatus.REQUIRES_ATTENTION}),
    OperationStatus.PAUSED: frozenset({OperationStatus.QUEUED, OperationStatus.CANCELLING, OperationStatus.CANCELLED, OperationStatus.REQUIRES_ATTENTION}),
    OperationStatus.CANCELLING: frozenset({OperationStatus.CANCELLED, OperationStatus.COMPLETED_WITH_ERRORS, OperationStatus.FAILED, OperationStatus.REQUIRES_ATTENTION}),
    OperationStatus.INTERRUPTED: frozenset({OperationStatus.QUEUED, OperationStatus.CANCELLING, OperationStatus.CANCELLED, OperationStatus.REQUIRES_ATTENTION}),
    OperationStatus.REQUIRES_ATTENTION: frozenset({OperationStatus.QUEUED, OperationStatus.CANCELLING, OperationStatus.CANCELLED}),
    OperationStatus.COMPLETED: frozenset(),
    OperationStatus.COMPLETED_WITH_ERRORS: frozenset(),
    OperationStatus.FAILED: frozenset(),
    OperationStatus.CANCELLED: frozenset(),
}

ITEM_TRANSITIONS: dict[OperationItemStatus, frozenset[OperationItemStatus]] = {
    OperationItemStatus.PENDING: frozenset({OperationItemStatus.QUEUED, OperationItemStatus.CANCELLED, OperationItemStatus.OBSOLETE}),
    OperationItemStatus.QUEUED: frozenset({OperationItemStatus.RUNNING, OperationItemStatus.CANCELLED, OperationItemStatus.OBSOLETE}),
    OperationItemStatus.RUNNING: frozenset({OperationItemStatus.SUCCEEDED, OperationItemStatus.FAILED, OperationItemStatus.SKIPPED, OperationItemStatus.CANCELLED, OperationItemStatus.OBSOLETE}),
    OperationItemStatus.SUCCEEDED: frozenset(),
    OperationItemStatus.FAILED: frozenset(),
    OperationItemStatus.SKIPPED: frozenset(),
    OperationItemStatus.CANCELLED: frozenset(),
    OperationItemStatus.OBSOLETE: frozenset(),
}

TERMINAL_ITEM_STATUSES = frozenset({OperationItemStatus.SUCCEEDED, OperationItemStatus.FAILED, OperationItemStatus.SKIPPED, OperationItemStatus.CANCELLED, OperationItemStatus.OBSOLETE})
UNFINISHED_OPERATION_STATUSES = frozenset(set(OperationStatus) - {OperationStatus.COMPLETED, OperationStatus.COMPLETED_WITH_ERRORS, OperationStatus.FAILED, OperationStatus.CANCELLED})


def validate_operation_transition(current: OperationStatus, target: OperationStatus) -> None:
    if target not in OPERATION_TRANSITIONS[current]:
        raise InvalidOperationTransition(f"Operation cannot transition from {current} to {target}")


def validate_operation_item_transition(current: OperationItemStatus, target: OperationItemStatus) -> None:
    if target not in ITEM_TRANSITIONS[current]:
        raise InvalidOperationItemTransition(f"Operation item cannot transition from {current} to {target}")


def completion_status(item_statuses: list[OperationItemStatus]) -> OperationStatus | None:
    """Return the aggregate terminal state when every item has a final result."""
    if not item_statuses or any(status not in TERMINAL_ITEM_STATUSES for status in item_statuses):
        return None
    if any(status == OperationItemStatus.FAILED for status in item_statuses):
        return OperationStatus.COMPLETED_WITH_ERRORS if any(status in {OperationItemStatus.SUCCEEDED, OperationItemStatus.SKIPPED} for status in item_statuses) else OperationStatus.FAILED
    if any(status in {OperationItemStatus.CANCELLED, OperationItemStatus.OBSOLETE} for status in item_statuses):
        return None
    return OperationStatus.COMPLETED


@dataclass(frozen=True)
class OperationItemDraft:
    item_type: str
    item_id: str
    source_version: str | None = None
    stage: str | None = None
    id: str = field(default_factory=lambda: str(uuid4()))


@dataclass(frozen=True)
class OperationDraft:
    operation_type: str
    title: str
    scope_type: str | None = None
    scope_id: str | None = None
    initiator_type: str | None = None
    initiator_id: str | None = None
    stage: str | None = None
    can_pause: bool = False
    can_cancel: bool = False
    can_resume: bool = False
    can_retry_failed: bool = False
    can_continue: bool = False
    requires_confirmation: bool = False
    parameters: dict[str, Any] = field(default_factory=dict)
    parent_operation_id: str | None = None
    id: str = field(default_factory=lambda: str(uuid4()))


@dataclass(frozen=True)
class Operation:
    id: str
    operation_type: str
    title: str
    status: OperationStatus
    scope_type: str | None
    scope_id: str | None
    initiator_type: str | None
    initiator_id: str | None
    total_items: int
    processed_items: int
    succeeded_items: int
    failed_items: int
    skipped_items: int
    progress_percent: int
    stage: str | None
    can_pause: bool
    can_cancel: bool
    can_resume: bool
    can_retry_failed: bool
    can_continue: bool
    requires_confirmation: bool
    parameters: dict[str, Any]
    result: dict[str, Any] | None
    error_code: str | None
    user_message: str | None
    version: int
    parent_operation_id: str | None


@dataclass(frozen=True)
class OperationItem:
    id: str
    operation_id: str
    item_type: str
    item_id: str
    source_version: str | None
    status: OperationItemStatus
    stage: str | None
    attempt_count: int
    error_code: str | None
    user_message: str | None
    result: dict[str, Any] | None
