"""Durable events recorded together with PhotoHome state changes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class OutboxStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"


SUPPORTED_EVENT_TYPES = frozenset({
    "operation.completed", "operation.completed_with_errors", "operation.failed",
    "album.created", "album.renamed", "media.file.created", "media.file.moved",
    "media.file.removed",
})


@dataclass(frozen=True)
class OutboxEventDraft:
    event_id: str
    event_type: str
    aggregate_type: str
    aggregate_id: str
    payload: dict
    operation_id: str | None = None

    def __post_init__(self) -> None:
        if self.event_type not in SUPPORTED_EVENT_TYPES:
            raise ValueError("Unsupported outbox event type")
        if not self.event_id or not self.aggregate_type or not self.aggregate_id:
            raise ValueError("Outbox event identifiers are required")
        if self.payload.get("version") != 1:
            raise ValueError("Outbox payload must declare version 1")


@dataclass(frozen=True)
class OutboxEvent:
    id: int
    event_id: str
    event_type: str
    aggregate_type: str
    aggregate_id: str
    operation_id: str | None
    payload: dict
    status: OutboxStatus
    attempt_count: int
    available_at: str
    locked_at: str | None
    locked_by: str | None
