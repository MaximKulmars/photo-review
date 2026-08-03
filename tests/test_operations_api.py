from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from app.application.commands.operations import CreateOperationCommand
from app.application.services.operation_manager import OperationManager
from app.db import Database
from app.domain.diagnostics import DiagnosticRequest
from app.domain.operations import OperationDraft, OperationItemDraft
from app.infrastructure.database.operations import SqliteOperationRepository
from app.infrastructure.diagnostics import DiagnosticService
from app.web.operations_api import install_operations_api


def make_client(tmp_path):
    database = Database(tmp_path / "data" / "app.sqlite3")
    database.initialize()
    manager = OperationManager(SqliteOperationRepository(database))
    def require_login(x_access: str | None = Header(default=None)):
        if x_access != "allowed":
            raise HTTPException(401, "Authentication required")
    app = FastAPI()
    install_operations_api(app, manager, require_login, DiagnosticService(database))
    return TestClient(app), manager, database


def create_operation(manager, *, title="Copy", items=1):
    result = manager.create_operation(CreateOperationCommand(OperationDraft("copy", title, can_pause=True, can_cancel=True, can_retry_failed=True, can_continue=True, requires_confirmation=True), tuple(OperationItemDraft("media", f"media-{index}") for index in range(items))))
    return result.operation


def test_detail_access_actions_and_safe_fields(tmp_path):
    client, manager, _ = make_client(tmp_path)
    operation = create_operation(manager)
    manager.queue_operation(operation.id)
    manager.start_operation(operation.id)
    response = client.get(f"/api/operations/{operation.id}", headers={"X-Access": "allowed"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "running"
    assert payload["actions"]["can_pause"] is True
    assert payload["actions"]["requires_confirmation"] is True
    assert "parameters" not in payload and "error_code" not in payload


def test_list_filters_pagination_empty_and_completed_with_errors(tmp_path):
    client, manager, _ = make_client(tmp_path)
    successful = create_operation(manager, title="A")
    failed = create_operation(manager, title="B", items=2)
    manager.queue_operation(failed.id); manager.start_operation(failed.id)
    item = manager.repository.items_for(failed.id)
    manager.mark_item_running(failed.id, item[0].id); manager.complete_item(failed.id, item[0].id)
    manager.mark_item_running(failed.id, item[1].id); manager.fail_item(failed.id, item[1].id, diagnostic_code="test.failure")
    response = client.get("/api/operations", params={"status": "completed_with_errors", "has_errors": "true", "page": 1, "page_size": 1}, headers={"X-Access": "allowed"})
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["id"] == failed.id
    empty = client.get("/api/operations", params={"operation_type": "missing"}, headers={"X-Access": "allowed"})
    assert empty.status_code == 200 and empty.json()["items"] == []


def test_items_are_paginated_and_unknown_or_unavailable_operations_do_not_leak(tmp_path):
    client, manager, _ = make_client(tmp_path)
    operation = create_operation(manager, items=3)
    denied = client.get(f"/api/operations/{operation.id}")
    assert denied.status_code == 401
    assert client.get("/api/operations/no-such-operation", headers={"X-Access": "allowed"}).status_code == 404
    page = client.get(f"/api/operations/{operation.id}/items", params={"page_size": 2}, headers={"X-Access": "allowed"})
    assert page.status_code == 200
    assert len(page.json()["items"]) == 2 and page.json()["total"] == 3
    assert "source_version" not in page.json()["items"][0]


def test_bad_filters_and_repository_error_are_safe_and_diagnostic(tmp_path, monkeypatch):
    client, manager, database = make_client(tmp_path)
    assert client.get("/api/operations", params={"page": 0}, headers={"X-Access": "allowed"}).status_code == 422
    def fail(**kwargs):
        raise RuntimeError("database path /private/secret")
    monkeypatch.setattr(manager.repository, "list", fail)
    response = client.get("/api/operations", headers={"X-Access": "allowed"})
    assert response.status_code == 500
    assert "private" not in response.json()["detail"]
    assert database.one("SELECT event_code FROM diagnostic_events")["event_code"] == "system.unexpected_error"
