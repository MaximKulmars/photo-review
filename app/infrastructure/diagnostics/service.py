"""User diagnostics in SQLite and sanitized JSONL technical logging."""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import re
from dataclasses import asdict, replace
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterator

import structlog

from ...db import Database
from ...domain.diagnostics import (
    DiagnosticContext,
    DiagnosticEvent,
    DiagnosticRequest,
    DiagnosticSeverity,
    DiagnosticStatus,
    diagnostic_code,
)


_context: contextvars.ContextVar[DiagnosticContext] = contextvars.ContextVar("diagnostic_context", default=DiagnosticContext())
_secret_key = re.compile(r"(password|token|secret|cookie|authorization|api[_-]?key|connection[_-]?string)", re.I)
_absolute_path = re.compile(r"(?:[A-Za-z]:[\\/][^\s,;]+|/(?:[^\s,;]+))")


def _safe(value):
    if isinstance(value, Path):
        return "<PATH>"
    if isinstance(value, dict):
        return {str(key): "<REDACTED>" if _secret_key.search(str(key)) else _safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_safe(item) for item in value]
    if isinstance(value, str):
        return _absolute_path.sub("<PATH>", value)
    try:
        json.dumps(value)
        return value
    except TypeError:
        return repr(value)


@contextlib.contextmanager
def bind_diagnostic_context(**values: str | None) -> Iterator[DiagnosticContext]:
    current = _context.get()
    context = replace(current, **{key: value for key, value in values.items() if value is not None})
    token = _context.set(context)
    try:
        yield context
    finally:
        _context.reset(token)


def current_diagnostic_context() -> DiagnosticContext:
    return _context.get()


def configure_json_logging(log_root: Path) -> None:
    """Configure the local JSONL technical log with a 200 MB rotating budget."""
    log_root.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_root / "photohome.jsonl", maxBytes=20 * 1024 * 1024, backupCount=9, encoding="utf-8")
    handler.setFormatter(structlog.stdlib.ProcessorFormatter(processor=structlog.processors.JSONRenderer(), foreign_pre_chain=[structlog.stdlib.add_log_level, structlog.processors.TimeStamper(fmt="iso")]))
    logger = logging.getLogger("photohome")
    logger.handlers[:] = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    structlog.configure(processors=[structlog.stdlib.add_log_level, structlog.processors.TimeStamper(fmt="iso"), structlog.processors.format_exc_info, structlog.stdlib.ProcessorFormatter.wrap_for_formatter], logger_factory=structlog.stdlib.LoggerFactory(), wrapper_class=structlog.stdlib.BoundLogger, cache_logger_on_first_use=True)


def shutdown_json_logging() -> None:
    logger = logging.getLogger("photohome")
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)


class DiagnosticService:
    def __init__(self, database: Database, logger=None):
        self.database = database
        self.logger = logger or structlog.get_logger("photohome")

    def record(self, request: DiagnosticRequest) -> DiagnosticEvent:
        definition = diagnostic_code(request.event_code)
        context = request.context if request.context != DiagnosticContext() else current_diagnostic_context()
        technical_reference = context.correlation_id or context.operation_id or context.request_id
        try:
            self.logger.error("diagnostic_event", **_safe({"event_name": request.event_code, "component": request.component, "context": asdict(context), "technical_message": request.technical_message, "extras": request.extras, "error_code": definition.code, "retryable": definition.severity != DiagnosticSeverity.CRITICAL}))
        except Exception:
            pass
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM diagnostic_events WHERE event_code=? AND component=? AND object_type IS ? AND object_id IS ? AND status IN (?, ?)", (definition.code, request.component, request.object_type, request.object_id, DiagnosticStatus.ACTIVE, DiagnosticStatus.ACKNOWLEDGED)).fetchone()
            if row is None:
                connection.execute("INSERT INTO diagnostic_events(event_code,severity,component,title,user_message,suggested_action,technical_reference,object_type,object_id,operation_id,status) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (definition.code, definition.severity, request.component, definition.title, definition.user_message, definition.suggested_action, technical_reference, request.object_type, request.object_id, context.operation_id, DiagnosticStatus.ACTIVE))
                row = connection.execute("SELECT * FROM diagnostic_events WHERE id=last_insert_rowid()").fetchone()
            else:
                connection.execute("UPDATE diagnostic_events SET last_occurred_at=CURRENT_TIMESTAMP, occurrence_count=occurrence_count+1, technical_reference=?, operation_id=COALESCE(?, operation_id) WHERE id=?", (technical_reference, context.operation_id, row["id"]))
                row = connection.execute("SELECT * FROM diagnostic_events WHERE id=?", (row["id"],)).fetchone()
            return self._event(row)

    def set_status(self, event_id: int, status: DiagnosticStatus) -> DiagnosticEvent:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM diagnostic_events WHERE id=?", (event_id,)).fetchone()
            if row is None:
                raise LookupError(event_id)
            if row["severity"] == DiagnosticSeverity.CRITICAL and status == DiagnosticStatus.IGNORED:
                raise ValueError("Critical diagnostics cannot be ignored")
            connection.execute("UPDATE diagnostic_events SET status=?, resolved_at=CASE WHEN ?=? THEN CURRENT_TIMESTAMP ELSE resolved_at END WHERE id=?", (status, status, DiagnosticStatus.RESOLVED, event_id))
            return self._event(connection.execute("SELECT * FROM diagnostic_events WHERE id=?", (event_id,)).fetchone())

    @staticmethod
    def _event(row) -> DiagnosticEvent:
        return DiagnosticEvent(row["id"], row["event_code"], DiagnosticSeverity(row["severity"]), row["component"], row["title"], row["user_message"], row["suggested_action"], row["technical_reference"], row["operation_id"], row["occurrence_count"], DiagnosticStatus(row["status"]))
