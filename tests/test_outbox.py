from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.db import Database
from app.domain.outbox import OutboxEventDraft, OutboxStatus
from app.infrastructure.database.outbox import (
    OutboxProcessor,
    PermanentOutboxError,
    SqliteOutboxRepository,
    TransientOutboxError,
)


@pytest.fixture
def outbox(tmp_path):
    database = Database(tmp_path / "data" / "app.sqlite3")
    database.initialize()
    return database, SqliteOutboxRepository(database)


def draft(event_id="event-1", aggregate_id="album-1"):
    return OutboxEventDraft(event_id, "album.created", "album", aggregate_id, {"version": 1, "name": "Summer"}, "operation-1")


def test_subject_change_and_event_commit_together(outbox):
    database, repository = outbox
    with database.connect() as connection:
        connection.execute("INSERT INTO audit_log(action, relative_path, details) VALUES('album.created', 'Summer', 'album-1')")
        repository.add_in_transaction(connection, draft())
    assert database.one("SELECT COUNT(*) AS count FROM audit_log")["count"] == 1
    assert repository.get("event-1").status == OutboxStatus.PENDING


def test_subject_rollback_does_not_leave_event(outbox):
    database, repository = outbox
    with pytest.raises(RuntimeError):
        with database.connect() as connection:
            repository.add_in_transaction(connection, draft())
            raise RuntimeError("rollback")
    assert repository.get("event-1") is None


def test_delivery_is_idempotent_and_two_workers_cannot_claim_twice(outbox):
    _, repository = outbox
    repository.add(draft())
    handled = []
    processor = OutboxProcessor(repository, {"album.created": lambda event: handled.append(event.event_id)})
    assert processor.process_batch("worker-1") == {"processed": 1, "retried": 0, "failed": 0}
    assert processor.process_batch("worker-2") == {"processed": 0, "retried": 0, "failed": 0}
    assert handled == ["event-1"]


def test_transient_and_permanent_errors_are_diagnostic_and_bounded(outbox):
    _, repository = outbox
    repository.add(draft())
    retrying = OutboxProcessor(repository, {"album.created": lambda event: (_ for _ in ()).throw(TransientOutboxError("network"))}, max_attempts=2)
    assert retrying.process_batch("worker") == {"processed": 0, "retried": 1, "failed": 0}
    event = repository.get("event-1")
    assert event.status == OutboxStatus.PENDING and event.attempt_count == 1
    repository.add(draft("event-2", "album-2"))
    permanent = OutboxProcessor(repository, {"album.created": lambda event: (_ for _ in ()).throw(PermanentOutboxError("bad payload"))})
    assert permanent.process_batch("worker", limit=5)["failed"] == 1
    assert repository.get("event-2").status == OutboxStatus.FAILED


def test_expired_lease_recovers_after_worker_stop_and_keeps_aggregate_order(outbox):
    _, repository = outbox
    repository.add(draft("event-1"))
    repository.add(draft("event-2"))
    first = repository.claim("stopped-worker", now="2030-01-01T00:00:00+00:00")
    assert [event.event_id for event in first] == ["event-1"]
    recovered = repository.claim("new-worker", now="2030-01-01T00:02:00+00:00")
    assert [event.event_id for event in recovered] == ["event-1"]
    repository.mark_processed("event-1", "new-worker")
    next_event = repository.claim("new-worker", now="2030-01-01T00:02:01+00:00")
    assert [event.event_id for event in next_event] == ["event-2"]


def test_unknown_payload_version_is_not_retried(outbox):
    database, repository = outbox
    with database.connect() as connection:
        connection.execute("INSERT INTO outbox_events(event_id,event_type,aggregate_type,aggregate_id,payload,status,available_at) VALUES(?,?,?,?,?,?,?)", ("unknown-version", "album.created", "album", "album-1", '{"version":2}', "pending", datetime.now(timezone.utc).isoformat(timespec="seconds")))
    processor = OutboxProcessor(repository, {"album.created": lambda event: None})
    assert processor.process_batch("worker")["failed"] == 1
    event = repository.get("unknown-version")
    assert event.status == OutboxStatus.FAILED
    assert database.one("SELECT last_error_code FROM outbox_events WHERE event_id='unknown-version'")["last_error_code"] == "permanent_handler_error"
