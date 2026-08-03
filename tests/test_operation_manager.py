from __future__ import annotations

import pytest

from app.application.commands.operations import (
    CreateOperationCommand,
    OperationActionUnavailableError,
    OperationIdempotencyConflictError,
)
from app.application.services.operation_manager import OperationManager
from app.db import Database
from app.domain.operations import OperationDraft, OperationItemDraft, OperationItemStatus, OperationStatus
from app.infrastructure.database.operations import SqliteOperationRepository


@pytest.fixture
def manager(tmp_path):
    database = Database(tmp_path / "photo-review.sqlite3")
    database.initialize()
    return OperationManager(SqliteOperationRepository(database))


def _command(*, item_count: int = 1, key: str | None = None) -> CreateOperationCommand:
    return CreateOperationCommand(
        draft=OperationDraft(
            operation_type="media.move", title="Move photos", can_pause=True,
            can_cancel=True, can_resume=True, can_retry_failed=True, can_continue=True,
            parameters={"destination": "album-42"},
        ),
        items=tuple(OperationItemDraft(item_type="media", item_id=str(index)) for index in range(item_count)),
        idempotency_key=key,
    )


def _running(manager: OperationManager, *, item_count: int = 1):
    operation = manager.create_operation(_command(item_count=item_count)).operation
    manager.queue_operation(operation.id)
    return manager.start_operation(operation.id)


def _finish(manager: OperationManager, operation_id: str, item_id: str, status: OperationItemStatus):
    manager.mark_item_running(operation_id, item_id)
    if status == OperationItemStatus.SUCCEEDED:
        return manager.complete_item(operation_id, item_id, result={"ok": True})
    if status == OperationItemStatus.FAILED:
        return manager.fail_item(operation_id, item_id, diagnostic_code="media_write_failed")
    return manager.skip_item(operation_id, item_id)


def test_create_operation_is_idempotent_for_matching_key(manager):
    first = manager.create_operation(_command(key="delivery-1"))
    second = manager.create_operation(_command(key="delivery-1"))

    assert first.created is True
    assert second.created is False
    assert second.operation.id == first.operation.id


def test_idempotency_key_rejects_incompatible_command(manager):
    manager.create_operation(_command(key="delivery-1"))
    incompatible = _command(key="delivery-1")
    incompatible = CreateOperationCommand(
        draft=OperationDraft(operation_type="media.move", title="Different operation"),
        items=incompatible.items,
        idempotency_key=incompatible.idempotency_key,
    )

    with pytest.raises(OperationIdempotencyConflictError):
        manager.create_operation(incompatible)


def test_manager_updates_items_progress_and_terminal_status(manager):
    operation = _running(manager, item_count=2)
    first, second = manager.repository.items_for(operation.id)

    _finish(manager, operation.id, first.id, OperationItemStatus.SUCCEEDED)
    _finish(manager, operation.id, second.id, OperationItemStatus.FAILED)

    final = manager.repository.get(operation.id)
    assert final.status == OperationStatus.COMPLETED_WITH_ERRORS
    assert (final.total_items, final.processed_items, final.succeeded_items, final.failed_items, final.progress_percent) == (2, 2, 1, 1, 100)


def test_all_failed_items_produce_failed_operation(manager):
    operation = _running(manager)
    item = manager.repository.items_for(operation.id)[0]

    _finish(manager, operation.id, item.id, OperationItemStatus.FAILED)

    assert manager.repository.get(operation.id).status == OperationStatus.FAILED


def test_repeating_a_confirmed_transition_is_safe(manager):
    operation = manager.create_operation(_command()).operation
    queued = manager.queue_operation(operation.id)

    assert manager.queue_operation(operation.id) == queued


def test_pause_and_resume_are_explicit_safe_states(manager):
    operation = _running(manager)
    assert manager.request_pause(operation.id).status == OperationStatus.PAUSING
    assert manager.pause_operation(operation.id).status == OperationStatus.PAUSED
    assert manager.resume_operation(operation.id).status == OperationStatus.QUEUED


def test_cancellation_stops_pending_items_without_rolling_back_success(manager):
    operation = _running(manager, item_count=2)
    first, second = manager.repository.items_for(operation.id)
    _finish(manager, operation.id, first.id, OperationItemStatus.SUCCEEDED)

    manager.request_cancel(operation.id)
    manager.cancel_pending_items(operation.id)
    final = manager.finalize_operation(operation.id)

    assert final.status == OperationStatus.COMPLETED_WITH_ERRORS
    assert manager.repository.items_for(operation.id)[1].status == OperationItemStatus.CANCELLED


def test_cancelled_operation_does_not_start_new_items(manager):
    operation = _running(manager)
    item = manager.repository.items_for(operation.id)[0]
    manager.request_cancel(operation.id)

    with pytest.raises(OperationActionUnavailableError):
        manager.mark_item_running(operation.id, item.id)


def test_cancelling_only_pending_items_finishes_as_cancelled(manager):
    operation = _running(manager, item_count=2)

    manager.request_cancel(operation.id)
    items = manager.cancel_pending_items(operation.id)
    final = manager.finalize_operation(operation.id)

    assert final.status == OperationStatus.CANCELLED
    assert {item.status for item in items} == {OperationItemStatus.CANCELLED}


def test_retry_creates_child_with_only_failed_items(manager):
    operation = _running(manager, item_count=2)
    first, second = manager.repository.items_for(operation.id)
    _finish(manager, operation.id, first.id, OperationItemStatus.SUCCEEDED)
    _finish(manager, operation.id, second.id, OperationItemStatus.FAILED)

    retry = manager.retry_failed_items(operation.id).operation

    assert retry.parent_operation_id == operation.id
    assert [item.item_id for item in manager.repository.items_for(retry.id)] == [second.item_id]


def test_actions_are_computed_by_the_manager(manager):
    operation = _running(manager)

    actions = manager.get_available_actions(operation.id)

    assert actions.can_pause and actions.can_cancel and actions.can_view_details
    assert not actions.can_resume and not actions.can_retry_failed


def test_interrupted_operations_are_available_for_future_recovery(manager):
    operation = _running(manager)

    interrupted = manager.mark_interrupted(operation.id)

    assert interrupted.status == OperationStatus.INTERRUPTED
    assert [item.id for item in manager.unfinished_operations()] == [operation.id]
