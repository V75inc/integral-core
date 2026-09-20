"""OperationContext typing surface for app operation handlers."""

from __future__ import annotations

from typing import Any, Dict, Optional, Protocol, Tuple, runtime_checkable


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

    async def find_entries_in_track_type(
        self, track_type: str, entry_type: Optional[str] = None
    ) -> list[Any]: ...

    async def find_track_id_by_title(self, track_type: str) -> Optional[str]: ...

    async def create_entry(
        self,
        *,
        track_id: str,
        entry_type_key: str,
        title: str,
        custom_fields: Optional[Dict[str, Any]] = None,
        body: str = "",
    ) -> Optional[Any]: ...

    async def update_entry_fields(
        self, entry_id: str, custom_fields: Dict[str, Any]
    ) -> bool: ...

    async def conditional_update_entry_fields(
        self,
        entry_id: str,
        *,
        state_field: str,
        expected_state: str,
        updates: Dict[str, Any],
    ) -> Tuple[bool, Optional[str]]: ...

    async def emit_audit(self, action: str, details: Dict[str, Any]) -> None: ...

    async def find_entries(self, query: dict) -> list: ...

    async def get(self, object_ref: dict) -> Any: ...

    async def query(self, query_spec: dict) -> dict: ...

    async def invoke(self, operation_key: str, payload: dict | None = None) -> dict: ...
