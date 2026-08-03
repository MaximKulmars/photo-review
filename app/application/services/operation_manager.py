"""Application coordinator for the lifecycle of a PhotoHome operation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Sequence

from ..commands.operations import (
    AvailableOperationActions,
    CreateOperationCommand,
    OperationActionUnavailableError,
    OperationCreationResult,
    OperationIdempotencyConflictError,
    OperationItemOwnershipError,
)
from ..ports.operations import OperationRepository
from ...domain.operations import (
    Operation,
    OperationDraft,
    OperationItem,
    OperationItemDraft,
    OperationItemStatus,
    OperationStatus,
    TERMINAL_ITEM_STATUSES,
    IdempotencyConflictError,
)


class OperationManager:
    """Coordinates state changes without invoking a worker or filesystem adapter."""

    def __init__(self, repository: OperationRepository):
        self.repository = repository

    def create_operation(self, command: CreateOperationCommand) -> OperationCreationResult:
        if not command.draft.operation_type or not command.draft.title:
            raise ValueError("Operation type and title are required")
        if command.idempotency_key:
            try:
                operation, created = self.repository.create_idempotent(command.draft, command.items, command.idempotency_key, self._fingerprint(command))
            except IdempotencyConflictError as error:
                raise OperationIdempotencyConflictError(command.idempotency_key) from error
            return OperationCreationResult(operation, created)
        return OperationCreationResult(self.repository.create(command.draft, command.items), True)

    def add_items(self, operation_id: str, items: Sequence[OperationItemDraft]) -> Operation:
        return self.repository.add_items(operation_id, items)

    def queue_operation(self, operation_id: str) -> Operation:
        return self._transition(operation_id, OperationStatus.QUEUED)

    def start_operation(self, operation_id: str) -> Operation:
        return self._transition(operation_id, OperationStatus.RUNNING)

    def set_stage(self, operation_id: str, stage: str) -> Operation:
        if not stage.strip():
            raise ValueError("Operation stage is required")
        return self.repository.set_stage(operation_id, stage)

    def mark_item_running(self, operation_id: str, item_id: str, *, stage: str | None = None) -> OperationItem:
        self._require_running(operation_id)
        item = self._item_for(operation_id, item_id)
        if item.status == OperationItemStatus.PENDING:
            item = self.repository.transition_item(item.id, OperationItemStatus.QUEUED)
        if item.status == OperationItemStatus.RUNNING:
            return item
        return self._transition_item(operation_id, item_id, OperationItemStatus.RUNNING, stage=stage)

    def complete_item(self, operation_id: str, item_id: str, *, stage: str | None = None, result: dict | None = None) -> OperationItem:
        self._require_running(operation_id)
        return self._transition_item(operation_id, item_id, OperationItemStatus.SUCCEEDED, stage=stage, result=result)

    def fail_item(self, operation_id: str, item_id: str, *, diagnostic_code: str, stage: str | None = None) -> OperationItem:
        self._require_running(operation_id)
        if not diagnostic_code:
            raise ValueError("A stable diagnostic code is required")
        return self._transition_item(operation_id, item_id, OperationItemStatus.FAILED, stage=stage, error_code=diagnostic_code)

    def skip_item(self, operation_id: str, item_id: str, *, stage: str | None = None) -> OperationItem:
        self._require_running(operation_id)
        return self._transition_item(operation_id, item_id, OperationItemStatus.SKIPPED, stage=stage)

    def request_pause(self, operation_id: str) -> Operation:
        operation = self._require(operation_id)
        if not self.get_available_actions(operation_id).can_pause:
            raise OperationActionUnavailableError("pause")
        return self._transition(operation.id, OperationStatus.PAUSING)

    def pause_operation(self, operation_id: str) -> Operation:
        return self._transition(operation_id, OperationStatus.PAUSED)

    def resume_operation(self, operation_id: str) -> Operation:
        if not self.get_available_actions(operation_id).can_resume:
            raise OperationActionUnavailableError("resume")
        return self._transition(operation_id, OperationStatus.QUEUED)

    def request_cancel(self, operation_id: str) -> Operation:
        if not self.get_available_actions(operation_id).can_cancel:
            raise OperationActionUnavailableError("cancel")
        return self._transition(operation_id, OperationStatus.CANCELLING)

    def cancel_pending_items(self, operation_id: str) -> list[OperationItem]:
        if self._require(operation_id).status != OperationStatus.CANCELLING:
            raise OperationActionUnavailableError("cancel_pending_items")
        return self.repository.cancel_pending_items(operation_id)

    def finalize_operation(self, operation_id: str) -> Operation:
        operation = self._require(operation_id)
        if operation.status not in {OperationStatus.RUNNING, OperationStatus.CANCELLING}:
            return operation
        items = self.repository.items_for(operation_id)
        if not items or any(item.status not in TERMINAL_ITEM_STATUSES for item in items):
            return operation
        if operation.status == OperationStatus.CANCELLING:
            if all(item.status in {OperationItemStatus.CANCELLED, OperationItemStatus.OBSOLETE} for item in items):
                return self._transition(operation_id, OperationStatus.CANCELLED)
            if any(item.status in {OperationItemStatus.SUCCEEDED, OperationItemStatus.SKIPPED} for item in items):
                return self._transition(operation_id, OperationStatus.COMPLETED_WITH_ERRORS)
            return self._transition(operation_id, OperationStatus.FAILED)
        return self._require(operation_id)

    def retry_failed_items(self, operation_id: str) -> OperationCreationResult:
        operation = self._require(operation_id)
        if not self.get_available_actions(operation_id).can_retry_failed:
            raise OperationActionUnavailableError("retry_failed_items")
        failed = [item for item in self.repository.items_for(operation_id) if item.status == OperationItemStatus.FAILED]
        if not failed:
            raise OperationActionUnavailableError("retry_failed_items")
        draft = OperationDraft(
            operation_type=operation.operation_type, title=f"Retry: {operation.title}", scope_type=operation.scope_type, scope_id=operation.scope_id,
            initiator_type=operation.initiator_type, initiator_id=operation.initiator_id, stage=operation.stage, can_pause=operation.can_pause,
            can_cancel=operation.can_cancel, can_resume=operation.can_resume, can_retry_failed=operation.can_retry_failed,
            can_continue=operation.can_continue, requires_confirmation=operation.requires_confirmation, parameters=operation.parameters,
            parent_operation_id=operation.id,
        )
        items = tuple(OperationItemDraft(item_type=item.item_type, item_id=item.item_id, source_version=item.source_version, stage=item.stage) for item in failed)
        return self.create_operation(CreateOperationCommand(draft=draft, items=items))

    def get_available_actions(self, operation_id: str) -> AvailableOperationActions:
        operation = self._require(operation_id)
        has_failed = any(item.status == OperationItemStatus.FAILED for item in self.repository.items_for(operation_id))
        return AvailableOperationActions(
            can_pause=operation.status == OperationStatus.RUNNING and operation.can_pause,
            can_resume=operation.status in {OperationStatus.PAUSED, OperationStatus.INTERRUPTED, OperationStatus.REQUIRES_ATTENTION} and (operation.can_resume or operation.can_continue),
            can_cancel=operation.status in {OperationStatus.CREATED, OperationStatus.QUEUED, OperationStatus.RUNNING, OperationStatus.PAUSING, OperationStatus.PAUSED} and operation.can_cancel,
            can_retry_failed=operation.status in {OperationStatus.FAILED, OperationStatus.COMPLETED_WITH_ERRORS} and operation.can_retry_failed and has_failed,
            can_continue=operation.status in {OperationStatus.INTERRUPTED, OperationStatus.REQUIRES_ATTENTION} and operation.can_continue,
            can_view_details=True,
            requires_confirmation=operation.requires_confirmation,
        )

    def mark_interrupted(self, operation_id: str) -> Operation:
        operation = self._require(operation_id)
        if operation.status not in {OperationStatus.QUEUED, OperationStatus.RUNNING, OperationStatus.PAUSING}:
            raise OperationActionUnavailableError("interrupt")
        return self._transition(operation_id, OperationStatus.INTERRUPTED)

    def unfinished_operations(self) -> list[Operation]:
        return self.repository.unfinished()

    def _transition(self, operation_id: str, target: OperationStatus) -> Operation:
        operation = self._require(operation_id)
        if operation.status == target:
            return operation
        return self.repository.transition(operation_id, target, expected_version=operation.version)

    def _transition_item(self, operation_id: str, item_id: str, target: OperationItemStatus, **kwargs) -> OperationItem:
        item = self._item_for(operation_id, item_id)
        if item.status == target:
            return item
        return self.repository.transition_item(item_id, target, **kwargs)

    def _item_for(self, operation_id: str, item_id: str) -> OperationItem:
        item = next((item for item in self.repository.items_for(operation_id) if item.id == item_id), None)
        if item is None:
            raise OperationItemOwnershipError(item_id)
        return item

    def _require_running(self, operation_id: str) -> Operation:
        operation = self._require(operation_id)
        if operation.status != OperationStatus.RUNNING:
            raise OperationActionUnavailableError("run_item")
        return operation

    def _require(self, operation_id: str) -> Operation:
        operation = self.repository.get(operation_id)
        if operation is None:
            raise OperationActionUnavailableError("operation_not_found")
        return operation

    @staticmethod
    def _fingerprint(command: CreateOperationCommand) -> str:
        draft = asdict(command.draft)
        draft.pop("id")
        items = [dict(item_type=item.item_type, item_id=item.item_id, source_version=item.source_version, stage=item.stage) for item in command.items]
        payload = json.dumps({"draft": draft, "items": items}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
