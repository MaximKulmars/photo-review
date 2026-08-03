from __future__ import annotations

from app.application.commands.file_operations import FileCommandType, FileOperationCommand
from app.application.commands.operations import CreateOperationCommand
from app.domain.locks import ResourceLockRequest
from app.domain.operations import OperationDraft, OperationItemDraft
from app.infrastructure.database.outbox import OutboxProcessor, SqliteOutboxRepository
from app.domain.outbox import OutboxEventDraft, OutboxStatus

from .core_fixtures import CoreEnvironment, core_environment


def test_operation_executor_lock_outbox_and_diagnostics_round_trip(core_environment: CoreEnvironment):
    environment = core_environment
    (environment.photos / "source.jpg").write_bytes(b"photo")
    creation = environment.manager.create_operation(CreateOperationCommand(OperationDraft("move", "Move media", can_cancel=True), (OperationItemDraft("media", "media-1"),)))
    operation = creation.operation
    item = environment.manager.repository.items_for(operation.id)[0]
    environment.manager.queue_operation(operation.id)
    environment.manager.start_operation(operation.id)
    environment.manager.mark_item_running(operation.id, item.id)
    lock = environment.manager.acquire_resource_locks(environment.locks, operation.id, [ResourceLockRequest("media", "media-1", item.id)]).locks[0]
    result = environment.executor.execute(FileOperationCommand("command-1", operation.id, item.id, FileCommandType.MOVE, "photos", "source.jpg", "photos", "target.jpg", idempotency_key="command-1"), lock=lock)
    environment.manager.complete_item(operation.id, item.id, result={"status": result.status})
    with environment.database.connect() as connection:
        SqliteOutboxRepository(environment.database).add_in_transaction(connection, OutboxEventDraft("event-1", "media.file.moved", "media", "media-1", {"version": 1}, operation.id))
    delivered = []
    processor = OutboxProcessor(SqliteOutboxRepository(environment.database), {"media.file.moved": lambda event: delivered.append(event.event_id)})
    assert processor.process_batch("integration-worker") == {"processed": 1, "retried": 0, "failed": 0}
    assert (environment.photos / "target.jpg").read_bytes() == b"photo"
    assert not (environment.photos / "source.jpg").exists()
    assert environment.manager.repository.get(operation.id).status == "completed"
    assert not environment.locks.owns(lock)
    assert delivered == ["event-1"]
    assert SqliteOutboxRepository(environment.database).get("event-1").status == OutboxStatus.PROCESSED


def test_core_fixture_never_uses_environment_library_paths(core_environment: CoreEnvironment, monkeypatch):
    monkeypatch.setenv("PHOTO_REVIEW_PHOTOS", "C:/user-library-must-not-be-used")
    assert core_environment.photos.is_relative_to(core_environment.root)
    assert core_environment.database.path.is_relative_to(core_environment.root)
    assert core_environment.huey_database.is_relative_to(core_environment.root)
