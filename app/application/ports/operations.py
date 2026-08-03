"""Port for persistence of user-visible long-running operations."""

from typing import Protocol, Sequence

from ...domain.operations import Operation, OperationDraft, OperationItem, OperationItemDraft, OperationItemStatus, OperationStatus


class OperationRepository(Protocol):
    """Stores and retrieves operation state without choosing a database."""

    def create(self, draft: OperationDraft, items: Sequence[OperationItemDraft] = ()) -> Operation: ...

    def get(self, operation_id: str) -> Operation | None: ...

    def items_for(self, operation_id: str) -> list[OperationItem]: ...

    def transition(self, operation_id: str, status: OperationStatus, *, stage: str | None = None, expected_version: int | None = None) -> Operation: ...

    def transition_item(self, item_id: str, status: OperationItemStatus, *, stage: str | None = None) -> OperationItem: ...

    def unfinished(self) -> list[Operation]: ...
