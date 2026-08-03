"""Read-only, versioned operation-state API for future server-rendered UI."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from ..application.services.operation_manager import OperationManager
from ..domain.diagnostics import DiagnosticRequest
from ..domain.operations import Operation, OperationItem, OperationStatus
from ..infrastructure.diagnostics import DiagnosticService
from ..infrastructure.diagnostics.service import _safe


API_VERSION = "v1"


class ActionsResponse(BaseModel):
    can_pause: bool
    can_resume: bool
    can_cancel: bool
    can_retry_failed: bool
    can_continue: bool
    can_view_details: bool = True
    requires_confirmation: bool


class OperationResponse(BaseModel):
    api_version: str = API_VERSION
    id: str
    operation_type: str
    title: str
    status: str
    stage: str | None
    total_items: int
    processed_items: int
    succeeded_items: int
    failed_items: int
    skipped_items: int
    progress_percent: int
    created_at: str | None
    queued_at: str | None
    started_at: str | None
    finished_at: str | None
    result: dict | None
    user_message: str | None
    parent_operation_id: str | None
    actions: ActionsResponse


class ItemResponse(BaseModel):
    id: str
    item_type: str
    item_id: str
    status: str
    stage: str | None
    attempt_count: int
    user_message: str | None
    result: dict | None


class OperationListResponse(BaseModel):
    api_version: str = API_VERSION
    items: list[OperationResponse]
    page: int
    page_size: int
    total: int


class OperationItemsResponse(BaseModel):
    api_version: str = API_VERSION
    items: list[ItemResponse]
    page: int
    page_size: int
    total: int


def _operation_response(manager: OperationManager, operation: Operation) -> OperationResponse:
    available = manager.get_available_actions(operation.id)
    return OperationResponse(
        id=operation.id, operation_type=operation.operation_type, title=operation.title,
        status=operation.status, stage=operation.stage, total_items=operation.total_items,
        processed_items=operation.processed_items, succeeded_items=operation.succeeded_items,
        failed_items=operation.failed_items, skipped_items=operation.skipped_items,
        progress_percent=operation.progress_percent, created_at=operation.created_at,
        queued_at=operation.queued_at, started_at=operation.started_at, finished_at=operation.finished_at,
        result=_safe(operation.result) if operation.result else None, user_message=operation.user_message,
        parent_operation_id=operation.parent_operation_id,
        actions=ActionsResponse(can_pause=available.can_pause, can_resume=available.can_resume, can_cancel=available.can_cancel, can_retry_failed=available.can_retry_failed, can_continue=available.can_continue, requires_confirmation=available.requires_confirmation),
    )


def _item_response(item: OperationItem) -> ItemResponse:
    return ItemResponse(id=item.id, item_type=item.item_type, item_id=item.item_id, status=item.status, stage=item.stage, attempt_count=item.attempt_count, user_message=item.user_message, result=_safe(item.result) if item.result else None)


def install_operations_api(app: FastAPI, manager: OperationManager, require_login, diagnostics: DiagnosticService) -> None:
    protected = [Depends(require_login)]

    @app.get("/api/operations/{operation_id}", response_model=OperationResponse, dependencies=protected, tags=["operations"])
    def get_operation(operation_id: str):
        operation = manager.repository.get(operation_id)
        if operation is None:
            raise HTTPException(404, "Operation not found")
        return _operation_response(manager, operation)

    @app.get("/api/operations", response_model=OperationListResponse, dependencies=protected, tags=["operations"])
    def list_operations(status: Annotated[list[OperationStatus] | None, Query()] = None, operation_type: Annotated[str | None, Query(min_length=1, max_length=100)] = None, created_from: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}-\d{2}")] = None, created_to: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}-\d{2}")] = None, has_errors: bool | None = None, page: Annotated[int, Query(ge=1)] = 1, page_size: Annotated[int, Query(ge=1, le=100)] = 25):
        try:
            operations, total = manager.repository.list(statuses=tuple(status or ()), operation_type=operation_type, created_from=created_from, created_to=created_to, has_errors=has_errors, limit=page_size, offset=(page - 1) * page_size)
        except Exception as error:
            diagnostics.record(DiagnosticRequest("system.unexpected_error", "operations_api", technical_message=type(error).__name__))
            raise HTTPException(500, "Operation list is temporarily unavailable") from error
        return OperationListResponse(items=[_operation_response(manager, operation) for operation in operations], page=page, page_size=page_size, total=total)

    @app.get("/api/operations/{operation_id}/items", response_model=OperationItemsResponse, dependencies=protected, tags=["operations"])
    def list_operation_items(operation_id: str, page: Annotated[int, Query(ge=1)] = 1, page_size: Annotated[int, Query(ge=1, le=100)] = 50):
        if manager.repository.get(operation_id) is None:
            raise HTTPException(404, "Operation not found")
        items, total = manager.repository.items_page(operation_id, limit=page_size, offset=(page - 1) * page_size)
        return OperationItemsResponse(items=[_item_response(item) for item in items], page=page, page_size=page_size, total=total)
