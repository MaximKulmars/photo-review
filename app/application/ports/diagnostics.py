"""Port for user-facing diagnostic events."""

from typing import Protocol


class DiagnosticJournal(Protocol):
    """Records diagnostics without selecting a storage backend."""
