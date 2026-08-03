"""Idempotent startup recovery driven by PhotoHome state, never Huey history."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from ...domain.diagnostics import DiagnosticRequest
from ...domain.operations import OperationItemStatus, OperationStatus, TERMINAL_ITEM_STATUSES


class RecoveryDecision(StrEnum):
    CONFIRMED_SUCCEEDED = "confirmed_succeeded"
    CONFIRMED_FAILED = "confirmed_failed"
    REQUEUE = "requeue"
    CONTINUE = "continue"
    CLEANUP_SAFE = "cleanup_safe"
    REQUIRES_ATTENTION = "requires_attention"


@dataclass(frozen=True)
class RecoveryReport:
    operation_id: str
    decisions: tuple[RecoveryDecision, ...]


class OperationRecoveryService:
    def __init__(self, manager, database, queue, diagnostics, *, temp_roots: tuple[Path, ...] = ()):
        self.manager = manager
        self.database = database
        self.queue = queue
        self.diagnostics = diagnostics
        self.temp_roots = tuple(root.resolve() for root in temp_roots)

    def recover(self) -> list[RecoveryReport]:
        return [self.recover_operation(operation.id) for operation in self.manager.unfinished_operations()]

    def recover_operation(self, operation_id: str) -> RecoveryReport:
        operation = self.manager.repository.get(operation_id)
        if operation is None or operation.status == OperationStatus.PAUSED:
            return RecoveryReport(operation_id, ())
        if operation.status == OperationStatus.CANCELLING:
            self.manager.cancel_pending_items(operation_id)
            self.manager.finalize_operation(operation_id)
            self._record(operation_id, None, RecoveryDecision.CONFIRMED_FAILED, {"proof": "cancellation_finalized"})
            return RecoveryReport(operation_id, (RecoveryDecision.CONFIRMED_FAILED,))
        decisions: list[RecoveryDecision] = []
        items = self.manager.repository.items_for(operation_id)
        unknown = False
        requeue = False
        for item in items:
            if item.status in TERMINAL_ITEM_STATUSES:
                continue
            journal = self.database.one("SELECT status, result_json FROM file_execution_commands WHERE operation_item_id=? ORDER BY updated_at DESC LIMIT 1", (item.id,))
            if journal and journal["status"] == "succeeded":
                if operation.status == OperationStatus.RUNNING and item.status == OperationItemStatus.RUNNING:
                    result = json.loads(journal["result_json"]) if journal["result_json"] else None
                    self.manager.complete_item(operation_id, item.id, stage="recovered", result=result)
                decisions.append(RecoveryDecision.CONFIRMED_SUCCEEDED)
                self._record(operation_id, item.id, RecoveryDecision.CONFIRMED_SUCCEEDED, {"proof": "executor_journal"})
            elif journal and journal["status"] == "needs_reconciliation":
                unknown = True
                decisions.append(RecoveryDecision.REQUIRES_ATTENTION)
                self._record(operation_id, item.id, RecoveryDecision.REQUIRES_ATTENTION, {"proof": "executor_needs_reconciliation"})
            elif item.status in {OperationItemStatus.PENDING, OperationItemStatus.QUEUED}:
                requeue = True
                decisions.append(RecoveryDecision.REQUEUE)
            else:
                unknown = True
                decisions.append(RecoveryDecision.REQUIRES_ATTENTION)
                self._record(operation_id, item.id, RecoveryDecision.REQUIRES_ATTENTION, {"proof": "in_progress_without_confirmation"})

        if unknown:
            self._requires_attention(operation_id)
            self._cleanup_owned_temps(operation_id, decisions)
        elif requeue:
            self._requeue(operation_id)
        elif not items:
            self._requeue(operation_id)
        return RecoveryReport(operation_id, tuple(decisions))

    def _requeue(self, operation_id: str) -> None:
        operation = self.manager.repository.get(operation_id)
        already_requested = self.database.one("SELECT 1 FROM operation_recovery_decisions WHERE operation_id=? AND decision=? LIMIT 1", (operation_id, RecoveryDecision.REQUEUE))
        if operation.status == OperationStatus.QUEUED and already_requested:
            return
        if operation.status in {OperationStatus.RUNNING, OperationStatus.PAUSING}:
            operation = self.manager.mark_interrupted(operation_id)
        if operation.status == OperationStatus.INTERRUPTED:
            self.manager.repository.transition(operation_id, OperationStatus.QUEUED, expected_version=operation.version)
        elif operation.status == OperationStatus.CREATED:
            self.manager.queue_operation(operation_id)
        self.queue.enqueue_operation(operation_id)
        self._record(operation_id, None, RecoveryDecision.REQUEUE, {"proof": "idempotent_recovery_dispatch"})

    def _requires_attention(self, operation_id: str) -> None:
        operation = self.manager.repository.get(operation_id)
        if operation.status in {OperationStatus.RUNNING, OperationStatus.PAUSING}:
            operation = self.manager.mark_interrupted(operation_id)
        if operation.status == OperationStatus.INTERRUPTED:
            self.manager.repository.transition(operation_id, OperationStatus.REQUIRES_ATTENTION, expected_version=operation.version)
        self.diagnostics.record(DiagnosticRequest("file_operation.needs_reconciliation", "operation_recovery", object_type="operation", object_id=operation_id))

    def _cleanup_owned_temps(self, operation_id: str, decisions: list[RecoveryDecision]) -> None:
        marker = f".photohome-{operation_id}-"
        for root in self.temp_roots:
            if not root.exists():
                continue
            for temporary in root.rglob("*.tmp"):
                if marker in temporary.name and temporary.is_file():
                    temporary.unlink()
                    decisions.append(RecoveryDecision.CLEANUP_SAFE)
                    self._record(operation_id, None, RecoveryDecision.CLEANUP_SAFE, {"proof": "owned_temp_file"})

    def _record(self, operation_id: str, item_id: str | None, decision: RecoveryDecision, evidence: dict) -> None:
        with self.database.connect() as connection:
            connection.execute("INSERT INTO operation_recovery_decisions(operation_id,operation_item_id,decision,evidence_json) VALUES(?,?,?,?)", (operation_id, item_id, decision, json.dumps(evidence, sort_keys=True)))
