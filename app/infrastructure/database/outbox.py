"""SQLite transactional outbox. Huey is delivery, never the source of truth."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Callable, Mapping

from ...db import Database
from ...domain.outbox import OutboxEvent, OutboxEventDraft, OutboxStatus


class TransientOutboxError(RuntimeError):
    """A handler failure that may be retried with bounded backoff."""


class PermanentOutboxError(RuntimeError):
    """A non-retryable handler or payload failure."""


OutboxHandler = Callable[[OutboxEvent], None]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SqliteOutboxRepository:
    def __init__(self, database: Database):
        self.database = database

    def add(self, draft: OutboxEventDraft) -> OutboxEvent:
        with self.database.connect() as connection:
            return self.add_in_transaction(connection, draft)

    def add_in_transaction(self, connection, draft: OutboxEventDraft) -> OutboxEvent:
        payload = json.dumps(draft.payload, ensure_ascii=False, separators=(",", ":"))
        if len(payload.encode("utf-8")) > 64 * 1024:
            raise ValueError("Outbox payload must remain small")
        connection.execute(
            "INSERT INTO outbox_events(event_id,event_type,aggregate_type,aggregate_id,operation_id,payload,status,available_at) VALUES(?,?,?,?,?,?,?,?)",
            (draft.event_id, draft.event_type, draft.aggregate_type, draft.aggregate_id, draft.operation_id, payload, OutboxStatus.PENDING, _now()),
        )
        return self._by_event_id(connection, draft.event_id)

    def claim(self, worker_id: str, *, limit: int = 25, lease_seconds: int = 60, now: str | None = None) -> list[OutboxEvent]:
        if limit < 1 or limit > 100:
            raise ValueError("Outbox batch size must be between 1 and 100")
        now = now or _now()
        expiry = (datetime.fromisoformat(now) - timedelta(seconds=lease_seconds)).isoformat(timespec="seconds")
        with self.database.connect() as connection:
            connection.execute("UPDATE outbox_events SET status=?, locked_at=NULL, locked_by=NULL WHERE status=? AND locked_at < ?", (OutboxStatus.PENDING, OutboxStatus.PROCESSING, expiry))
            rows = connection.execute(
                """
                SELECT event.* FROM outbox_events AS event
                WHERE event.status=? AND event.available_at<=?
                  AND NOT EXISTS (
                    SELECT 1 FROM outbox_events AS earlier
                    WHERE earlier.aggregate_type=event.aggregate_type
                      AND earlier.aggregate_id=event.aggregate_id
                      AND earlier.id<event.id
                      AND earlier.status IN (?, ?)
                  )
                ORDER BY event.id LIMIT ?
                """,
                (OutboxStatus.PENDING, now, OutboxStatus.PENDING, OutboxStatus.PROCESSING, limit),
            ).fetchall()
            claimed: list[OutboxEvent] = []
            for row in rows:
                updated = connection.execute("UPDATE outbox_events SET status=?, attempt_count=attempt_count+1, locked_at=?, locked_by=? WHERE id=? AND status=?", (OutboxStatus.PROCESSING, now, worker_id, row["id"], OutboxStatus.PENDING))
                if updated.rowcount:
                    claimed.append(self._row(connection.execute("SELECT * FROM outbox_events WHERE id=?", (row["id"],)).fetchone()))
            return claimed

    def mark_processed(self, event_id: str, worker_id: str) -> None:
        self._finish(event_id, worker_id, OutboxStatus.PROCESSED)

    def mark_failed(self, event_id: str, worker_id: str, code: str, message: str) -> None:
        self._finish(event_id, worker_id, OutboxStatus.FAILED, code, message)

    def retry(self, event: OutboxEvent, worker_id: str, code: str, message: str, *, max_attempts: int = 3) -> bool:
        if event.attempt_count >= max_attempts:
            self.mark_failed(event.event_id, worker_id, code, message)
            return False
        delay = 2 ** (event.attempt_count - 1)
        available = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat(timespec="seconds")
        with self.database.connect() as connection:
            updated = connection.execute("UPDATE outbox_events SET status=?, available_at=?, locked_at=NULL, locked_by=NULL, last_error_code=?, last_error_message=? WHERE event_id=? AND status=? AND locked_by=?", (OutboxStatus.PENDING, available, code, message[:500], event.event_id, OutboxStatus.PROCESSING, worker_id))
            if updated.rowcount != 1:
                raise RuntimeError("Outbox event lease was lost")
        return True

    def get(self, event_id: str) -> OutboxEvent | None:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM outbox_events WHERE event_id=?", (event_id,)).fetchone()
            return self._row(row) if row else None

    def _finish(self, event_id: str, worker_id: str, status: OutboxStatus, code: str | None = None, message: str | None = None) -> None:
        with self.database.connect() as connection:
            updated = connection.execute("UPDATE outbox_events SET status=?, processed_at=?, locked_at=NULL, locked_by=NULL, last_error_code=?, last_error_message=? WHERE event_id=? AND status=? AND locked_by=?", (status, _now(), code, message[:500] if message else None, event_id, OutboxStatus.PROCESSING, worker_id))
            if updated.rowcount != 1:
                raise RuntimeError("Outbox event lease was lost")

    @staticmethod
    def _row(row) -> OutboxEvent:
        return OutboxEvent(row["id"], row["event_id"], row["event_type"], row["aggregate_type"], row["aggregate_id"], row["operation_id"], json.loads(row["payload"]), OutboxStatus(row["status"]), row["attempt_count"], row["available_at"], row["locked_at"], row["locked_by"])

    def _by_event_id(self, connection, event_id: str) -> OutboxEvent:
        return self._row(connection.execute("SELECT * FROM outbox_events WHERE event_id=?", (event_id,)).fetchone())


class OutboxProcessor:
    def __init__(self, repository: SqliteOutboxRepository, handlers: Mapping[str, OutboxHandler], *, max_attempts: int = 3):
        self.repository = repository
        self.handlers = handlers
        self.max_attempts = max_attempts

    def process_batch(self, worker_id: str, *, limit: int = 25) -> dict[str, int]:
        result = {"processed": 0, "retried": 0, "failed": 0}
        for event in self.repository.claim(worker_id, limit=limit):
            try:
                if event.payload.get("version") != 1:
                    raise PermanentOutboxError("unsupported_payload_version")
                handler = self.handlers.get(event.event_type)
                if handler is None:
                    raise PermanentOutboxError("unregistered_event_type")
                handler(event)
            except TransientOutboxError as error:
                if self.repository.retry(event, worker_id, "transient_handler_error", str(error), max_attempts=self.max_attempts):
                    result["retried"] += 1
                else:
                    result["failed"] += 1
            except PermanentOutboxError as error:
                self.repository.mark_failed(event.event_id, worker_id, "permanent_handler_error", str(error))
                result["failed"] += 1
            except Exception as error:
                self.repository.mark_failed(event.event_id, worker_id, "unexpected_handler_error", type(error).__name__)
                result["failed"] += 1
            else:
                self.repository.mark_processed(event.event_id, worker_id)
                result["processed"] += 1
        return result
