"""SQLite-backed exclusive resource locks with lease validation."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable

from ...db import Database
from ...domain.locks import LockAcquireResult, LockConflict, LockMode, ResourceLock, ResourceLockRequest
from ...domain.operations import UNFINISHED_OPERATION_STATUSES


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SqliteResourceLockRepository:
    def __init__(self, database: Database, *, clock: Callable[[], datetime] = _utcnow):
        self.database = database
        self.clock = clock

    def acquire(self, owner_operation_id: str, resources: Iterable[ResourceLockRequest], *, ttl_seconds: int = 60) -> LockAcquireResult:
        requests = tuple(sorted(set(resources)))
        if not requests:
            return LockAcquireResult(())
        now = self.clock()
        expires_at = (now + timedelta(seconds=ttl_seconds)).isoformat(timespec="seconds")
        now_text = now.isoformat(timespec="seconds")
        try:
            with self.database.connect() as connection:
                acquired: list[ResourceLock] = []
                for request in requests:
                    row = connection.execute("SELECT * FROM resource_locks WHERE resource_type=? AND resource_id=? AND lock_mode=?", (request.resource_type, request.resource_id, request.lock_mode)).fetchone()
                    if row is not None:
                        existing = self._row(row)
                        if existing.owner_operation_id == owner_operation_id and existing.owner_item_id == request.owner_item_id:
                            acquired.append(existing)
                            continue
                        if existing.expires_at <= now_text:
                            if self._operation_is_finished(connection, existing.owner_operation_id):
                                connection.execute("DELETE FROM resource_locks WHERE resource_type=? AND resource_id=? AND lock_mode=? AND token=?", (existing.resource_type, existing.resource_id, existing.lock_mode, existing.token))
                            else:
                                raise _LockConflict(LockConflict(request.resource_type, request.resource_id, existing.owner_operation_id, "review_owner_operation"))
                        else:
                            raise _LockConflict(LockConflict(request.resource_type, request.resource_id, existing.owner_operation_id, "wait_or_review"))
                    token = secrets.token_urlsafe(24)
                    try:
                        connection.execute("INSERT INTO resource_locks(resource_type,resource_id,lock_mode,owner_operation_id,owner_item_id,token,acquired_at,heartbeat_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?)", (request.resource_type, request.resource_id, request.lock_mode, owner_operation_id, request.owner_item_id, token, now_text, now_text, expires_at))
                    except Exception:
                        # Another SQLite writer won the same resource after our read.
                        row = connection.execute("SELECT * FROM resource_locks WHERE resource_type=? AND resource_id=? AND lock_mode=?", (request.resource_type, request.resource_id, request.lock_mode)).fetchone()
                        if row is not None:
                            existing = self._row(row)
                            raise _LockConflict(LockConflict(request.resource_type, request.resource_id, existing.owner_operation_id, "wait_or_review"))
                        raise
                    acquired.append(ResourceLock(request.resource_type, request.resource_id, request.lock_mode, owner_operation_id, request.owner_item_id, token, now_text, now_text, expires_at))
                return LockAcquireResult(tuple(acquired))
        except _LockConflict as error:
            return LockAcquireResult((), (error.conflict,))

    def heartbeat(self, lock: ResourceLock, *, ttl_seconds: int = 60) -> bool:
        now = self.clock()
        with self.database.connect() as connection:
            updated = connection.execute("UPDATE resource_locks SET heartbeat_at=?, expires_at=? WHERE resource_type=? AND resource_id=? AND lock_mode=? AND owner_operation_id=? AND token=?", ((now).isoformat(timespec="seconds"), (now + timedelta(seconds=ttl_seconds)).isoformat(timespec="seconds"), lock.resource_type, lock.resource_id, lock.lock_mode, lock.owner_operation_id, lock.token))
            return updated.rowcount == 1

    def release(self, lock: ResourceLock) -> bool:
        with self.database.connect() as connection:
            deleted = connection.execute("DELETE FROM resource_locks WHERE resource_type=? AND resource_id=? AND lock_mode=? AND owner_operation_id=? AND token=?", (lock.resource_type, lock.resource_id, lock.lock_mode, lock.owner_operation_id, lock.token))
            return deleted.rowcount == 1

    def release_operation(self, owner_operation_id: str) -> int:
        with self.database.connect() as connection:
            deleted = connection.execute("DELETE FROM resource_locks WHERE owner_operation_id=?", (owner_operation_id,))
            return deleted.rowcount

    def owns(self, lock: ResourceLock) -> bool:
        with self.database.connect() as connection:
            row = connection.execute("SELECT expires_at FROM resource_locks WHERE resource_type=? AND resource_id=? AND lock_mode=? AND owner_operation_id=? AND token=?", (lock.resource_type, lock.resource_id, lock.lock_mode, lock.owner_operation_id, lock.token)).fetchone()
            return row is not None and row["expires_at"] > self.clock().isoformat(timespec="seconds")

    @staticmethod
    def _operation_is_finished(connection, operation_id: str) -> bool:
        row = connection.execute("SELECT status FROM operations WHERE id=?", (operation_id,)).fetchone()
        return row is None or row["status"] not in UNFINISHED_OPERATION_STATUSES

    @staticmethod
    def _row(row) -> ResourceLock:
        return ResourceLock(row["resource_type"], row["resource_id"], LockMode(row["lock_mode"]), row["owner_operation_id"], row["owner_item_id"], row["token"], row["acquired_at"], row["heartbeat_at"], row["expires_at"])


class _LockConflict(RuntimeError):
    def __init__(self, conflict: LockConflict):
        self.conflict = conflict
