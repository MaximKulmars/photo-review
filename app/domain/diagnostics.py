"""Stable, user-safe diagnostics shared by the new application core."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class DiagnosticSeverity(StrEnum):
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class DiagnosticStatus(StrEnum):
    ACTIVE = "active"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    IGNORED = "ignored"


@dataclass(frozen=True)
class DiagnosticContext:
    correlation_id: str | None = None
    request_id: str | None = None
    operation_id: str | None = None
    operation_item_id: str | None = None
    task_id: str | None = None
    command_id: str | None = None
    event_id: str | None = None
    component: str | None = None
    stage: str | None = None


@dataclass(frozen=True)
class DiagnosticCode:
    code: str
    severity: DiagnosticSeverity
    title: str
    user_message: str
    suggested_action: str


CATALOG = {
    "file_operation.needs_reconciliation": DiagnosticCode("file_operation.needs_reconciliation", DiagnosticSeverity.CRITICAL, "Требуется проверка файловой операции", "Состояние файла подтверждено не полностью.", "Проверьте состояние операции и запустите сверку."),
    "outbox.permanent_failure": DiagnosticCode("outbox.permanent_failure", DiagnosticSeverity.ERROR, "Фоновое событие не обработано", "Система не смогла завершить фоновую обработку.", "Откройте диагностику и повторите действие после проверки причины."),
    "worker.task_failure": DiagnosticCode("worker.task_failure", DiagnosticSeverity.ERROR, "Фоновая задача завершилась ошибкой", "Фоновая обработка не завершилась.", "Проверьте операцию и повторите только безопасное действие."),
    "system.unexpected_error": DiagnosticCode("system.unexpected_error", DiagnosticSeverity.ERROR, "Техническая ошибка", "Система столкнулась с технической ошибкой.", "Повторите безопасное действие или откройте диагностику."),
}


def diagnostic_code(code: str) -> DiagnosticCode:
    return CATALOG.get(code, CATALOG["system.unexpected_error"])


@dataclass(frozen=True)
class DiagnosticEvent:
    id: int
    event_code: str
    severity: DiagnosticSeverity
    component: str
    title: str
    user_message: str
    suggested_action: str
    technical_reference: str | None
    operation_id: str | None
    occurrence_count: int
    status: DiagnosticStatus


@dataclass(frozen=True)
class DiagnosticRequest:
    event_code: str
    component: str
    object_type: str | None = None
    object_id: str | None = None
    context: DiagnosticContext = field(default_factory=DiagnosticContext)
    technical_message: str | None = None
    extras: dict = field(default_factory=dict)
