"""Registered technical tasks. They receive stable operation identifiers only."""

from __future__ import annotations

import logging

from ...application.services.operation_manager import OperationManager
from ...config import load_config
from ...db import Database
from ...domain.operations import OperationStatus
from ..database.operations import SqliteOperationRepository
from ..database.outbox import OutboxProcessor, SqliteOutboxRepository
from .huey_app import create_huey


logger = logging.getLogger("photohome.huey")
worker_config = load_config()
RETRY_COUNT = worker_config.retry_count
RETRY_DELAY_SECONDS = worker_config.retry_delay_seconds
huey = create_huey(worker_config)


class TransientOperationTaskError(RuntimeError):
    """An infrastructure failure that Huey may retry with bounded backoff."""


def operation_manager() -> OperationManager:
    config = load_config()
    database = Database(config.database_path)
    database.initialize()
    return OperationManager(SqliteOperationRepository(database))


@huey.task(retries=RETRY_COUNT, retry_delay=RETRY_DELAY_SECONDS, retry_backoff=2)
def process_outbox(worker_id: str = "huey", limit: int = 25) -> dict[str, int]:
    """Deliver a bounded outbox batch; events remain recoverable in PhotoHome DB."""
    config = load_config()
    database = Database(config.database_path)
    database.initialize()
    processor = OutboxProcessor(SqliteOutboxRepository(database), handlers={})
    return processor.process_batch(worker_id, limit=limit)


@huey.task(retries=RETRY_COUNT, retry_delay=RETRY_DELAY_SECONDS, retry_backoff=2)
def process_operation(operation_id: str) -> dict[str, str]:
    manager = operation_manager()
    operation = manager.repository.get(operation_id)
    if operation is None:
        logger.warning("operation_task_missing", extra={"operation_id": operation_id})
        return {"operation_id": operation_id, "outcome": "missing"}
    if operation.status in {OperationStatus.COMPLETED, OperationStatus.COMPLETED_WITH_ERRORS, OperationStatus.FAILED, OperationStatus.CANCELLED}:
        logger.info("operation_task_terminal", extra={"operation_id": operation_id})
        return {"operation_id": operation_id, "outcome": "terminal"}
    if operation.status == OperationStatus.CREATED:
        operation = manager.queue_operation(operation_id)
    if operation.status == OperationStatus.QUEUED:
        operation = manager.start_operation(operation_id)
    logger.info("operation_task_dispatched", extra={"operation_id": operation_id, "status": operation.status})
    return {"operation_id": operation_id, "outcome": "running"}
