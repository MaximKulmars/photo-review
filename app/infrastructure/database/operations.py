"""SQLite implementation of the operation repository."""

from __future__ import annotations

import json
from collections.abc import Sequence

from ...db import Database
from ...domain.operations import (
    Operation,
    OperationDraft,
    OperationItem,
    OperationItemDraft,
    OperationItemStatus,
    OperationStatus,
    TERMINAL_ITEM_STATUSES,
    UNFINISHED_OPERATION_STATUSES,
    completion_status,
    validate_operation_item_transition,
    validate_operation_transition,
)


MAX_PARAMETERS_BYTES = 64 * 1024


class OperationNotFoundError(LookupError):
    pass


class ConcurrentOperationUpdateError(RuntimeError):
    pass


class SqliteOperationRepository:
    def __init__(self, database: Database):
        self.database = database

    def create(self, draft: OperationDraft, items: Sequence[OperationItemDraft] = ()) -> Operation:
        parameters_json = json.dumps(draft.parameters, ensure_ascii=False, separators=(",", ":"))
        if len(parameters_json.encode("utf-8")) > MAX_PARAMETERS_BYTES:
            raise ValueError("Operation parameters must remain small")
        items = tuple(items)
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO operations(
                    id, operation_type, scope_type, scope_id, initiator_type, initiator_id,
                    status, title, stage, total_items, can_pause, can_cancel, can_resume,
                    can_retry_failed, can_continue, requires_confirmation, parameters_json,
                    parent_operation_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (draft.id, draft.operation_type, draft.scope_type, draft.scope_id, draft.initiator_type, draft.initiator_id, OperationStatus.CREATED, draft.title, draft.stage, len(items), int(draft.can_pause), int(draft.can_cancel), int(draft.can_resume), int(draft.can_retry_failed), int(draft.can_continue), int(draft.requires_confirmation), parameters_json, draft.parent_operation_id),
            )
            for item in items:
                connection.execute(
                    """
                    INSERT INTO operation_items(id, operation_id, item_type, item_id, source_version, status, stage)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (item.id, draft.id, item.item_type, item.item_id, item.source_version, OperationItemStatus.PENDING, item.stage),
                )
            return self._operation(connection, draft.id)

    def get(self, operation_id: str) -> Operation | None:
        with self.database.connect() as connection:
            return self._operation(connection, operation_id, required=False)

    def items_for(self, operation_id: str) -> list[OperationItem]:
        with self.database.connect() as connection:
            rows = connection.execute("SELECT * FROM operation_items WHERE operation_id=? ORDER BY created_at, id", (operation_id,)).fetchall()
            return [self._item(row) for row in rows]

    def transition(self, operation_id: str, status: OperationStatus, *, stage: str | None = None, expected_version: int | None = None) -> Operation:
        with self.database.connect() as connection:
            operation = self._operation(connection, operation_id)
            validate_operation_transition(operation.status, status)
            version = operation.version if expected_version is None else expected_version
            timestamp_column = {OperationStatus.QUEUED: "queued_at", OperationStatus.RUNNING: "started_at", OperationStatus.COMPLETED: "finished_at", OperationStatus.COMPLETED_WITH_ERRORS: "finished_at", OperationStatus.FAILED: "finished_at", OperationStatus.CANCELLED: "finished_at"}.get(status)
            assignments = ["status=?", "stage=COALESCE(?, stage)", "version=version+1", "updated_at=CURRENT_TIMESTAMP"]
            if timestamp_column:
                assignments.append(f"{timestamp_column}=COALESCE({timestamp_column}, CURRENT_TIMESTAMP)")
            result = connection.execute(f"UPDATE operations SET {', '.join(assignments)} WHERE id=? AND version=?", (status, stage, operation_id, version))
            if result.rowcount != 1:
                raise ConcurrentOperationUpdateError(operation_id)
            return self._operation(connection, operation_id)

    def transition_item(self, item_id: str, status: OperationItemStatus, *, stage: str | None = None) -> OperationItem:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM operation_items WHERE id=?", (item_id,)).fetchone()
            if row is None:
                raise OperationNotFoundError(item_id)
            item = self._item(row)
            validate_operation_item_transition(item.status, status)
            assignments = ["status=?", "stage=COALESCE(?, stage)", "updated_at=CURRENT_TIMESTAMP"]
            if status == OperationItemStatus.RUNNING:
                assignments.extend(["attempt_count=attempt_count+1", "started_at=COALESCE(started_at, CURRENT_TIMESTAMP)"])
            if status in TERMINAL_ITEM_STATUSES:
                assignments.append("finished_at=COALESCE(finished_at, CURRENT_TIMESTAMP)")
            connection.execute(f"UPDATE operation_items SET {', '.join(assignments)} WHERE id=?", (status, stage, item_id))
            self._refresh_counts(connection, item.operation_id)
            refreshed = connection.execute("SELECT * FROM operation_items WHERE id=?", (item_id,)).fetchone()
            return self._item(refreshed)

    def unfinished(self) -> list[Operation]:
        with self.database.connect() as connection:
            placeholders = ",".join("?" for _ in UNFINISHED_OPERATION_STATUSES)
            rows = connection.execute(f"SELECT * FROM operations WHERE status IN ({placeholders}) ORDER BY created_at", tuple(UNFINISHED_OPERATION_STATUSES)).fetchall()
            return [self._operation_from_row(row) for row in rows]

    def _refresh_counts(self, connection, operation_id: str) -> None:
        rows = connection.execute("SELECT status FROM operation_items WHERE operation_id=?", (operation_id,)).fetchall()
        statuses = [OperationItemStatus(row["status"]) for row in rows]
        processed = sum(status in TERMINAL_ITEM_STATUSES for status in statuses)
        succeeded = statuses.count(OperationItemStatus.SUCCEEDED)
        failed = statuses.count(OperationItemStatus.FAILED)
        skipped = statuses.count(OperationItemStatus.SKIPPED)
        total = len(statuses)
        progress = int(processed * 100 / total) if total else 0
        operation = self._operation(connection, operation_id)
        target = completion_status(statuses)
        next_status = operation.status
        finished = ""
        if target and target != operation.status:
            validate_operation_transition(operation.status, target)
            next_status = target
            finished = ", finished_at=COALESCE(finished_at, CURRENT_TIMESTAMP)"
        connection.execute(
            f"""
            UPDATE operations
            SET total_items=?, processed_items=?, succeeded_items=?, failed_items=?, skipped_items=?,
                progress_percent=?, status=?, version=version+1, updated_at=CURRENT_TIMESTAMP{finished}
            WHERE id=? AND version=?
            """,
            (total, processed, succeeded, failed, skipped, progress, next_status, operation_id, operation.version),
        )

    def _operation(self, connection, operation_id: str, *, required: bool = True) -> Operation | None:
        row = connection.execute("SELECT * FROM operations WHERE id=?", (operation_id,)).fetchone()
        if row is None:
            if required:
                raise OperationNotFoundError(operation_id)
            return None
        return self._operation_from_row(row)

    @staticmethod
    def _operation_from_row(row) -> Operation:
        return Operation(
            id=row["id"], operation_type=row["operation_type"], title=row["title"], status=OperationStatus(row["status"]),
            scope_type=row["scope_type"], scope_id=row["scope_id"], initiator_type=row["initiator_type"], initiator_id=row["initiator_id"],
            total_items=row["total_items"], processed_items=row["processed_items"], succeeded_items=row["succeeded_items"], failed_items=row["failed_items"],
            skipped_items=row["skipped_items"], progress_percent=row["progress_percent"], stage=row["stage"], can_pause=bool(row["can_pause"]),
            can_cancel=bool(row["can_cancel"]), can_resume=bool(row["can_resume"]), can_retry_failed=bool(row["can_retry_failed"]),
            can_continue=bool(row["can_continue"]), requires_confirmation=bool(row["requires_confirmation"]), parameters=json.loads(row["parameters_json"]),
            result=json.loads(row["result_json"]) if row["result_json"] else None, error_code=row["error_code"], user_message=row["user_message"],
            version=row["version"], parent_operation_id=row["parent_operation_id"],
        )

    @staticmethod
    def _item(row) -> OperationItem:
        return OperationItem(
            id=row["id"], operation_id=row["operation_id"], item_type=row["item_type"], item_id=row["item_id"], source_version=row["source_version"],
            status=OperationItemStatus(row["status"]), stage=row["stage"], attempt_count=row["attempt_count"], error_code=row["error_code"],
            user_message=row["user_message"], result=json.loads(row["result_json"]) if row["result_json"] else None,
        )
