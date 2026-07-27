from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class JobKind(StrEnum):
    ANALYSIS = "analysis"


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class BackgroundJob:
    id: int
    kind: JobKind
    state: JobState
    payload: dict[str, Any] | None = None
