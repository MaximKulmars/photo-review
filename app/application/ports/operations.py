"""Port for persistence of user-visible long-running operations."""

from typing import Protocol


class OperationRepository(Protocol):
    """Stores and retrieves operation state without choosing a database."""
