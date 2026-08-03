"""Port for publishing application events."""

from typing import Protocol


class EventPublisher(Protocol):
    """Publishes events without selecting a transport."""
