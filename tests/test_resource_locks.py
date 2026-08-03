from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from threading import Barrier, Thread

from app.application.commands.operations import CreateOperationCommand
from app.application.services.operation_manager import OperationManager
from app.db import Database
from app.domain.locks import ResourceLockRequest
from app.domain.operations import OperationDraft, OperationStatus
from app.infrastructure.database.locks import SqliteResourceLockRepository
from app.infrastructure.database.operations import SqliteOperationRepository


def setup(tmp_path, now=None):
    database = Database(tmp_path / "data" / "app.sqlite3")
    database.initialize()
    clock = (lambda: now[0]) if now else None
    locks = SqliteResourceLockRepository(database, **({"clock": clock} if clock else {}))
    manager = OperationManager(SqliteOperationRepository(database))
    operation = manager.create_operation(CreateOperationCommand(OperationDraft("move", "Move", can_cancel=True))).operation
    return database, locks, manager, operation


def test_conflicting_and_independent_resources_with_repeat_owner(tmp_path):
    _, locks, manager, first = setup(tmp_path)
    second = manager.create_operation(CreateOperationCommand(OperationDraft("move", "Second"))).operation
    request = ResourceLockRequest("media", "media-1")
    acquired = manager.acquire_resource_locks(locks, first.id, [request])
    repeated = manager.acquire_resource_locks(locks, first.id, [request])
    conflict = manager.acquire_resource_locks(locks, second.id, [request])
    independent = manager.acquire_resource_locks(locks, second.id, [ResourceLockRequest("media", "media-2")])
    assert acquired.acquired and repeated.locks[0].token == acquired.locks[0].token
    assert not conflict.acquired and conflict.conflicts[0].owner_operation_id == first.id
    assert independent.acquired


def test_token_heartbeat_release_and_deterministic_multi_lock_order(tmp_path):
    _, locks, manager, operation = setup(tmp_path)
    result = manager.acquire_resource_locks(locks, operation.id, [ResourceLockRequest("album", "b"), ResourceLockRequest("media", "a")])
    assert [(lock.resource_type, lock.resource_id) for lock in result.locks] == [("album", "b"), ("media", "a")]
    assert locks.heartbeat(result.locks[0])
    assert not locks.release(replace(result.locks[0], token="foreign-token"))
    assert locks.release(result.locks[0])
    assert not locks.owns(result.locks[0])


def test_expired_lock_requires_owner_review_until_operation_is_finished(tmp_path):
    now = [datetime(2030, 1, 1, tzinfo=timezone.utc)]
    _, locks, manager, owner = setup(tmp_path, now)
    lock = manager.acquire_resource_locks(locks, owner.id, [ResourceLockRequest("media", "media-1")]).locks[0]
    now[0] += timedelta(minutes=2)
    contender = manager.create_operation(CreateOperationCommand(OperationDraft("move", "Contender"))).operation
    active_conflict = manager.acquire_resource_locks(locks, contender.id, [ResourceLockRequest("media", "media-1")])
    assert active_conflict.conflicts[0].next_action == "review_owner_operation"
    manager.repository.transition(owner.id, OperationStatus.FAILED)
    released = manager.acquire_resource_locks(locks, contender.id, [ResourceLockRequest("media", "media-1")])
    assert released.acquired and released.locks[0].token != lock.token


def test_concurrent_sqlite_claim_has_one_owner_and_no_partial_multi_lock(tmp_path):
    database, locks, manager, first = setup(tmp_path)
    second = manager.create_operation(CreateOperationCommand(OperationDraft("move", "Second"))).operation
    barrier = Barrier(2)
    results = []
    def claim(operation_id):
        barrier.wait()
        results.append((operation_id, SqliteResourceLockRepository(database).acquire(operation_id, [ResourceLockRequest("media", "shared")])) )
    threads = [Thread(target=claim, args=(first.id,)), Thread(target=claim, args=(second.id,))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(result.acquired for _, result in results) == 1
    owner = next(operation_id for operation_id, result in results if result.acquired)
    blocked = second.id if owner == first.id else first.id
    partial = locks.acquire(blocked, [ResourceLockRequest("media", "free"), ResourceLockRequest("media", "shared")])
    assert not partial.acquired
    assert locks.acquire(owner, [ResourceLockRequest("media", "free")]).acquired


def test_terminal_operation_releases_its_resource_locks(tmp_path):
    database = Database(tmp_path / "data" / "app.sqlite3")
    database.initialize()
    locks = SqliteResourceLockRepository(database)
    manager = OperationManager(SqliteOperationRepository(database), locks)
    operation = manager.create_operation(CreateOperationCommand(OperationDraft("move", "Move"))).operation
    acquired = manager.acquire_resource_locks(locks, operation.id, [ResourceLockRequest("media", "media-1")])
    manager._transition(operation.id, OperationStatus.FAILED)
    assert not locks.owns(acquired.locks[0])
