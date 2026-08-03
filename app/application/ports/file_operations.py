"""Port for verified file-system changes."""

from typing import Protocol


class FileOperationExecutor(Protocol):
    """Executes file operations after application-level validation."""
