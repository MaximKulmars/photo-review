"""Typed commands and results for the Operation Manager."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...domain.operations import Operation, OperationDraft, OperationItemDraft


class OperationManagerError(RuntimeError):
    code = "operation_manager_error"


class OperationActionUnavailableError(OperationManagerError):
    code = "operation_action_unavailable"


class OperationIdempotencyConflictError(OperationManagerError):
    code = "operation_idempotency_conflict"


class OperationItemOwnershipError(OperationManagerError):
    code = "operation_item_ownership_error"


@dataclass(frozen=True)
class CreateOperationCommand:
    draft: OperationDraft
    items: tuple[OperationItemDraft, ...] = ()
    idempotency_key: str | None = None


@dataclass(frozen=True)
class OperationCreationResult:
    operation: Operation
    created: bool


@dataclass(frozen=True)
class AvailableOperationActions:
    can_pause: bool
    can_resume: bool
    can_cancel: bool
    can_retry_failed: bool
    can_continue: bool
    can_view_details: bool
    requires_confirmation: bool


OperationResult = dict[str, Any]
