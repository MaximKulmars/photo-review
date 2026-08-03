"""Huey adapter for the application background-queue port."""

class HueyBackgroundQueue:
    def __init__(self, enqueue=None):
        self._enqueue = enqueue

    def enqueue_operation(self, operation_id: str) -> str:
        if self._enqueue is None:
            from .tasks import process_operation

            self._enqueue = process_operation
        result = self._enqueue(operation_id)
        return str(result.id)
