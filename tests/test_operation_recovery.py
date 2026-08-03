from __future__ import annotations

import json

from app.application.commands.operations import CreateOperationCommand
from app.application.services.operation_manager import OperationManager
from app.application.services.operation_recovery import OperationRecoveryService, RecoveryDecision
from app.db import Database
from app.domain.operations import OperationDraft, OperationItemDraft, OperationStatus
from app.infrastructure.database.operations import SqliteOperationRepository


class Queue:
    def __init__(self): self.operations = []
    def enqueue_operation(self, operation_id): self.operations.append(operation_id); return operation_id


class Diagnostics:
    def __init__(self): self.requests = []
    def record(self, request): self.requests.append(request)


def setup(tmp_path, *, item_count=1):
    database = Database(tmp_path / "data" / "app.sqlite3")
    database.initialize()
    manager = OperationManager(SqliteOperationRepository(database))
    operation = manager.create_operation(CreateOperationCommand(OperationDraft("move", "Recover", can_pause=True, can_resume=True, can_cancel=True), tuple(OperationItemDraft("media", f"media-{number}") for number in range(item_count)))).operation
    queue, diagnostics = Queue(), Diagnostics()
    service = OperationRecoveryService(manager, database, queue, diagnostics, temp_roots=(tmp_path,))
    return database, manager, operation, queue, diagnostics, service


def test_queued_operation_is_requeued_once_after_worker_loss(tmp_path):
    _, manager, operation, queue, _, service = setup(tmp_path)
    manager.queue_operation(operation.id)
    first = service.recover_operation(operation.id)
    second = service.recover_operation(operation.id)
    assert RecoveryDecision.REQUEUE in first.decisions
    assert queue.operations == [operation.id]
    assert second.decisions == (RecoveryDecision.REQUEUE,)


def test_confirmed_executor_result_is_not_run_again(tmp_path):
    database, manager, operation, queue, _, service = setup(tmp_path)
    manager.queue_operation(operation.id); manager.start_operation(operation.id)
    item = manager.repository.items_for(operation.id)[0]
    manager.mark_item_running(operation.id, item.id)
    database.execute("INSERT INTO file_execution_commands(command_id,operation_id,operation_item_id,idempotency_key,fingerprint,status,result_json) VALUES(?,?,?,?,?,?,?)", ("command", operation.id, item.id, "key", "fingerprint", "succeeded", json.dumps({"verified": True})))
    report = service.recover_operation(operation.id)
    recovered = manager.repository.items_for(operation.id)[0]
    assert RecoveryDecision.CONFIRMED_SUCCEEDED in report.decisions
    assert recovered.status == "succeeded" and queue.operations == []


def test_unknown_result_requires_attention_and_cleans_owned_temp(tmp_path):
    database, manager, operation, _, diagnostics, service = setup(tmp_path)
    manager.queue_operation(operation.id); manager.start_operation(operation.id)
    item = manager.repository.items_for(operation.id)[0]
    manager.mark_item_running(operation.id, item.id)
    database.execute("INSERT INTO file_execution_commands(command_id,operation_id,operation_item_id,idempotency_key,fingerprint,status) VALUES(?,?,?,?,?,?)", ("command", operation.id, item.id, "key", "fingerprint", "needs_reconciliation"))
    temporary = tmp_path / f".photo.photohome-{operation.id}-part.tmp"
    temporary.write_bytes(b"partial")
    report = service.recover_operation(operation.id)
    assert manager.repository.get(operation.id).status == OperationStatus.REQUIRES_ATTENTION
    assert RecoveryDecision.REQUIRES_ATTENTION in report.decisions
    assert RecoveryDecision.CLEANUP_SAFE in report.decisions
    assert not temporary.exists() and diagnostics.requests


def test_paused_and_cancelling_operations_follow_safe_paths(tmp_path):
    _, manager, operation, queue, _, service = setup(tmp_path)
    manager.queue_operation(operation.id); manager.start_operation(operation.id)
    manager.request_pause(operation.id); manager.pause_operation(operation.id)
    assert service.recover_operation(operation.id).decisions == ()
    other = manager.create_operation(CreateOperationCommand(OperationDraft("move", "Cancel", can_cancel=True), (OperationItemDraft("media", "other"),))).operation
    manager.queue_operation(other.id); manager.request_cancel(other.id)
    report = service.recover_operation(other.id)
    assert report.decisions == (RecoveryDecision.CONFIRMED_FAILED,)
    assert manager.repository.get(other.id).status == OperationStatus.CANCELLED


def test_multiple_operations_include_running_unknown_and_safe_queue(tmp_path):
    _, manager, first, queue, _, service = setup(tmp_path)
    manager.queue_operation(first.id)
    second = manager.create_operation(CreateOperationCommand(OperationDraft("move", "Unknown"), (OperationItemDraft("media", "second"),))).operation
    manager.queue_operation(second.id); manager.start_operation(second.id)
    item = manager.repository.items_for(second.id)[0]
    manager.mark_item_running(second.id, item.id)
    reports = service.recover()
    assert {report.operation_id for report in reports} == {first.id, second.id}
    assert queue.operations == [first.id]
    assert manager.repository.get(second.id).status == OperationStatus.REQUIRES_ATTENTION
