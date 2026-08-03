"""Port for verified file-system changes."""

from typing import Protocol

from app.application.commands.file_operations import FileOperationCommand, FileOperationResult


class FileOperationExecutor(Protocol):
    """Executes file operations after application-level validation."""

    def execute(self, command: FileOperationCommand) -> FileOperationResult:
        """Apply one confirmed file operation exactly once when possible."""
