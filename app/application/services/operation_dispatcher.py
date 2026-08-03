"""Coordinates durable operation state with technical task delivery."""

from __future__ import annotations

from dataclasses import dataclass

from ..ports.background_tasks import BackgroundTaskQueue
from .operation_manager import OperationManager
from ...domain.operations import Operation


@dataclass(frozen=True)
class OperationDispatchResult:
    operation: Operation
    task_id: str


class OperationDispatcher:
    """Queues an existing operation without exposing a queue to its domain model."""

    def __init__(self, manager: OperationManager, queue: BackgroundTaskQueue):
        self.manager = manager
        self.queue = queue

    def enqueue_operation(self, operation_id: str) -> OperationDispatchResult:
        operation = self.manager.queue_operation(operation_id)
        return OperationDispatchResult(operation=operation, task_id=self.queue.enqueue_operation(operation.id))
