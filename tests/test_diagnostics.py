from __future__ import annotations

import json

import pytest

from app.db import Database
from app.domain.diagnostics import DiagnosticContext, DiagnosticRequest, DiagnosticStatus
from app.infrastructure.diagnostics.service import (
    DiagnosticService,
    _safe,
    bind_diagnostic_context,
    configure_json_logging,
)


class CapturingLogger:
    def __init__(self):
        self.events = []

    def error(self, name, **fields):
        self.events.append((name, fields))


@pytest.fixture
def service(tmp_path):
    database = Database(tmp_path / "data" / "app.sqlite3")
    database.initialize()
    logger = CapturingLogger()
    return DiagnosticService(database, logger), database, logger


def test_context_flows_through_a_web_worker_executor_chain(service):
    journal, _, logger = service
    with bind_diagnostic_context(correlation_id="request-1", request_id="http-1", operation_id="operation-1"):
        with bind_diagnostic_context(task_id="task-1", command_id="command-1", event_id="event-1", component="executor"):
            event = journal.record(DiagnosticRequest("file_operation.needs_reconciliation", "executor", "media", "media-1"))
    assert event.operation_id == "operation-1"
    fields = logger.events[0][1]
    assert fields["context"]["correlation_id"] == "request-1"
    assert fields["context"]["task_id"] == "task-1"


def test_secrets_paths_and_non_json_values_are_safe(service, tmp_path):
    journal, _, logger = service
    event = journal.record(DiagnosticRequest("worker.task_failure", "worker", technical_message=f"failed at {tmp_path}", extras={"token": "very-secret", "nested": {"password": "hidden"}, "path": tmp_path, "custom": object()}))
    fields = logger.events[0][1]
    assert event.user_message == "Фоновая обработка не завершилась."
    assert fields["extras"]["token"] == "<REDACTED>"
    assert fields["extras"]["nested"]["password"] == "<REDACTED>"
    assert fields["extras"]["path"] == "<PATH>"
    assert "<PATH>" in fields["technical_message"]
    assert isinstance(fields["extras"]["custom"], str)


def test_repeated_primary_cause_groups_but_different_object_does_not(service):
    journal, database, _ = service
    first = journal.record(DiagnosticRequest("outbox.permanent_failure", "outbox", "event", "event-1"))
    repeated = journal.record(DiagnosticRequest("outbox.permanent_failure", "outbox", "event", "event-1"))
    separate = journal.record(DiagnosticRequest("outbox.permanent_failure", "outbox", "event", "event-2"))
    assert first.id == repeated.id
    assert repeated.occurrence_count == 2
    assert separate.id != first.id
    assert database.one("SELECT COUNT(*) AS count FROM diagnostic_events")["count"] == 2


def test_critical_event_cannot_be_ignored_and_user_message_stays_safe(service):
    journal, _, _ = service
    event = journal.record(DiagnosticRequest("file_operation.needs_reconciliation", "executor", technical_message="Traceback: /secret/path"))
    assert "Traceback" not in event.user_message
    with pytest.raises(ValueError):
        journal.set_status(event.id, DiagnosticStatus.IGNORED)
    assert journal.set_status(event.id, DiagnosticStatus.ACKNOWLEDGED).status == DiagnosticStatus.ACKNOWLEDGED


def test_logger_failure_does_not_break_the_operation(service):
    journal, _, logger = service
    def fail(name, **fields):
        raise OSError("log volume unavailable")
    logger.error = fail
    event = journal.record(DiagnosticRequest("worker.task_failure", "worker"))
    assert event.event_code == "worker.task_failure"


def test_jsonl_logger_writes_machine_readable_record(tmp_path):
    configure_json_logging(tmp_path / "logs")
    from structlog import get_logger
    get_logger("photohome").info("diagnostic_test", component="test", correlation_id="correlation-1")
    line = (tmp_path / "logs" / "photohome.jsonl").read_text(encoding="utf-8").strip()
    payload = json.loads(line)
    assert payload["event"] == "diagnostic_test"
    assert payload["correlation_id"] == "correlation-1"
