"""OperationContext typing surface for app operation handlers."""

from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable


@runtime_checkable
class OperationContext(Protocol):
    """Context injected into app operation handlers by Core."""

    user_id: str
    workspace_id: str
    scope: str
    bundle_slug: str
    app_id: str
    operation_key: str
    idempotency_key: Optional[str]
    correlation_id: Optional[str]

    async def get_entry(self, entry_id: str) -> Any: ...

    async def find_entries(self, query: dict) -> list: ...

    async def get(self, object_ref: dict) -> Any: ...

    async def query(self, query_spec: dict) -> dict: ...

    async def invoke(self, operation_key: str, payload: dict | None = None) -> dict: ...
