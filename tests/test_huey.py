from __future__ import annotations

import importlib
from dataclasses import dataclass

from app.application.commands.operations import CreateOperationCommand
from app.application.services.operation_dispatcher import OperationDispatcher
from app.application.services.operation_manager import OperationManager
from app.config import Config
from app.db import Database
from app.domain.operations import OperationDraft, OperationStatus
from app.infrastructure.background.huey_app import create_huey
from app.infrastructure.background.huey_queue import HueyBackgroundQueue
from app.infrastructure.database.operations import SqliteOperationRepository


def _config(tmp_path, *, immediate: bool = False) -> Config:
    return Config(
        photos_root=tmp_path / "photos", videos_root=None, quarantine_root=tmp_path / "quarantine",
        data_root=tmp_path / "data", password="test", session_secret="test-secret", auth_enabled=False,
        port=8080, upload_max_files=50, upload_max_file_bytes=1024, upload_max_total_bytes=2048,
        huey_db_path=tmp_path / "queue" / "huey.sqlite3", huey_immediate=immediate,
    )


def _manager(config: Config) -> OperationManager:
    database = Database(config.database_path)
    database.initialize()
    return OperationManager(SqliteOperationRepository(database))


def test_huey_uses_a_separate_sqlite_file(tmp_path):
    config = _config(tmp_path)
    huey = create_huey(config)

    assert huey.storage.filename == str(config.huey_db_path)
    assert config.huey_db_path != config.database_path
    assert config.huey_db_path.parent.is_dir()


def test_persisted_queue_survives_a_new_huey_instance(tmp_path):
    config = _config(tmp_path)
    first = create_huey(config)

    @first.task()
    def pending_operation(operation_id: str):
        return operation_id

    pending_operation("operation-1")
    second = create_huey(config)

    assert first.storage.queue_size() == 1
    assert second.storage.queue_size() == 1


def test_dispatcher_uses_replaceable_application_queue(tmp_path):
    manager = _manager(_config(tmp_path))

    @dataclass
    class FakeQueue:
        operation_ids: list[str]

        def enqueue_operation(self, operation_id: str) -> str:
            self.operation_ids.append(operation_id)
            return "technical-task-1"

    operation = manager.create_operation(CreateOperationCommand(draft=OperationDraft(operation_type="infrastructure.noop", title="No-op"))).operation
    queue = FakeQueue([])
    result = OperationDispatcher(manager, queue).enqueue_operation(operation.id)

    assert result.operation.status == OperationStatus.QUEUED
    assert result.task_id == "technical-task-1"
    assert queue.operation_ids == [operation.id]


def test_huey_adapter_enqueues_only_operation_id():
    @dataclass
    class Result:
        id: str = "huey-task-1"

    received: list[str] = []
    queue = HueyBackgroundQueue(lambda operation_id: received.append(operation_id) or Result())

    assert queue.enqueue_operation("operation-1") == "huey-task-1"
    assert received == ["operation-1"]


def test_operation_task_uses_manager_and_skips_terminal_delivery(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setenv("PHOTO_REVIEW_DATA", str(config.data_root))
    monkeypatch.setenv("PHOTO_REVIEW_HUEY_DB", str(config.huey_db_path))
    tasks = importlib.import_module("app.infrastructure.background.tasks")
    manager = _manager(config)
    operation = manager.create_operation(CreateOperationCommand(draft=OperationDraft(operation_type="infrastructure.noop", title="No-op"))).operation

    assert tasks.process_operation.func(operation.id)["outcome"] == "running"
    assert manager.repository.get(operation.id).status == OperationStatus.RUNNING
    assert tasks.process_operation.func(operation.id)["outcome"] == "running"
    manager.repository.transition(operation.id, OperationStatus.FAILED, expected_version=manager.repository.get(operation.id).version)

    assert tasks.process_operation.func(operation.id)["outcome"] == "terminal"
    assert tasks.process_operation.settings["default_retries"] == 3
    assert tasks.process_operation.settings["default_retry_delay"] == 5


def test_operation_task_reports_a_missing_operation_without_retrying(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setenv("PHOTO_REVIEW_DATA", str(config.data_root))
    monkeypatch.setenv("PHOTO_REVIEW_HUEY_DB", str(config.huey_db_path))
    tasks = importlib.import_module("app.infrastructure.background.tasks")

    assert tasks.process_operation.func("missing-operation") == {"operation_id": "missing-operation", "outcome": "missing"}
