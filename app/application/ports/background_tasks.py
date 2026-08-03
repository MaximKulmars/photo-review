"""Port for technical background task delivery."""

from typing import Protocol


class BackgroundTaskQueue(Protocol):
    """Schedules background work without coupling to a queue implementation."""
