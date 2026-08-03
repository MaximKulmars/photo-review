from __future__ import annotations

import sqlite3

import pytest

from app.db import Database
from app.domain.operations import (
    ITEM_TRANSITIONS,
    OPERATION_TRANSITIONS,
    InvalidOperationItemTransition,
    InvalidOperationTransition,
    OperationDraft,
    OperationItemDraft,
    OperationItemStatus,
    OperationStatus,
    validate_operation_item_transition,
    validate_operation_transition,
)
from app.infrastructure.database.operations import (
    ConcurrentOperationUpdateError,
    SqliteOperationRepository,
)


@pytest.fixture
def repository(tmp_path):
    database = Database(tmp_path / "photo-review.sqlite3")
    database.initialize()
    return SqliteOperationRepository(database)


def _running_operation(repository: SqliteOperationRepository, item_count: int = 1):
    operation = repository.create(
        OperationDraft(operation_type="library.import", title="Import photos"),
        [OperationItemDraft(item_type="media", item_id=str(index)) for index in range(item_count)],
    )
    operation = repository.transition(operation.id, OperationStatus.QUEUED)
    repository.transition(operation.id, OperationStatus.RUNNING, expected_version=operation.version)
    return repository.get(operation.id)


def _run_item(repository: SqliteOperationRepository, item_id: str, final_status: OperationItemStatus):
    repository.transition_item(item_id, OperationItemStatus.QUEUED)
    repository.transition_item(item_id, OperationItemStatus.RUNNING)
    return repository.transition_item(item_id, final_status)


def test_migration_creates_operation_tables_and_indexes(tmp_path):
    database = Database(tmp_path / "photo-review.sqlite3")
    database.initialize()

    with database.connect() as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        indexes = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='index'")}

    assert {"operations", "operation_items", "media", "containers"} <= tables
    assert {"operations_status_idx", "operations_parent_idx", "operation_items_operation_idx", "operation_items_object_idx"} <= indexes
    assert database.schema_version() == 9


def test_existing_database_migrates_without_losing_legacy_tables(tmp_path):
    database = Database(tmp_path / "photo-review.sqlite3")
    database.initialize()
    database.rollback_schema(6, backup=False)
    with database.connect() as connection:
        connection.execute("INSERT INTO settings(key, value) VALUES (?, ?)", ("legacy-marker", "true"))

    database.initialize()
    database.initialize()

    assert database.one("SELECT value FROM settings WHERE key='legacy-marker'")["value"] == "true"
    assert database.one("SELECT name FROM sqlite_master WHERE type='table' AND name='operations'") is not None


def test_creates_mass_operation_with_items_not_a_large_json_list(repository):
    operation = repository.create(
        OperationDraft(operation_type="media.move", title="Move photos", parameters={"destination": "album-42"}),
        [OperationItemDraft(item_type="media", item_id=str(index)) for index in range(3)],
    )

    assert operation.status == OperationStatus.CREATED
    assert operation.total_items == 3
    assert len(repository.items_for(operation.id)) == 3


def test_operation_transitions_and_terminal_states_are_validated():
    for current, targets in OPERATION_TRANSITIONS.items():
        for target in targets:
            validate_operation_transition(current, target)
    with pytest.raises(InvalidOperationTransition):
        validate_operation_transition(OperationStatus.COMPLETED, OperationStatus.RUNNING)


def test_item_transitions_are_validated():
    for current, targets in ITEM_TRANSITIONS.items():
        for target in targets:
            validate_operation_item_transition(current, target)
    with pytest.raises(InvalidOperationItemTransition):
        validate_operation_item_transition(OperationItemStatus.SUCCEEDED, OperationItemStatus.RUNNING)


def test_item_results_complete_operation_and_count_partial_failures(repository):
    operation = _running_operation(repository, item_count=2)
    first, second = repository.items_for(operation.id)

    _run_item(repository, first.id, OperationItemStatus.SUCCEEDED)
    _run_item(repository, second.id, OperationItemStatus.FAILED)

    operation = repository.get(operation.id)
    assert operation.status == OperationStatus.COMPLETED_WITH_ERRORS
    assert (operation.processed_items, operation.succeeded_items, operation.failed_items, operation.progress_percent) == (2, 1, 1, 100)


def test_all_successful_items_complete_operation(repository):
    operation = _running_operation(repository, item_count=1)
    item = repository.items_for(operation.id)[0]

    _run_item(repository, item.id, OperationItemStatus.SUCCEEDED)

    assert repository.get(operation.id).status == OperationStatus.COMPLETED


def test_parent_operation_and_unfinished_query(repository):
    parent = repository.create(OperationDraft(operation_type="media.move", title="Initial"))
    child = repository.create(OperationDraft(operation_type="media.move", title="Retry", parent_operation_id=parent.id))
    repository.transition(parent.id, OperationStatus.QUEUED)
    repository.transition(parent.id, OperationStatus.RUNNING)
    repository.transition(parent.id, OperationStatus.FAILED)

    assert repository.get(child.id).parent_operation_id == parent.id
    assert [operation.id for operation in repository.unfinished()] == [child.id]


def test_compare_and_set_rejects_lost_update(repository):
    operation = repository.create(OperationDraft(operation_type="media.move", title="Move"))
    repository.transition(operation.id, OperationStatus.QUEUED, expected_version=operation.version)

    with pytest.raises(ConcurrentOperationUpdateError):
        repository.transition(operation.id, OperationStatus.RUNNING, expected_version=operation.version)


def test_create_rolls_back_when_an_item_fails(repository):
    draft = OperationDraft(operation_type="media.move", title="Move")
    duplicate = OperationItemDraft(item_type="media", item_id="same")

    with pytest.raises(sqlite3.IntegrityError):
        repository.create(draft, [duplicate, duplicate])

    assert repository.get(draft.id) is None


def test_item_transition_rolls_back_if_aggregate_refresh_fails(repository, monkeypatch):
    operation = _running_operation(repository)
    item = repository.items_for(operation.id)[0]
    repository.transition_item(item.id, OperationItemStatus.QUEUED)
    repository.transition_item(item.id, OperationItemStatus.RUNNING)
    before = repository.get(operation.id)

    def fail_refresh(*_):
        raise RuntimeError("simulated aggregate failure")

    monkeypatch.setattr(repository, "_refresh_counts", fail_refresh)
    with pytest.raises(RuntimeError, match="aggregate failure"):
        repository.transition_item(item.id, OperationItemStatus.SUCCEEDED)

    assert repository.items_for(operation.id)[0].status == OperationItemStatus.RUNNING
    assert repository.get(operation.id).processed_items == before.processed_items
