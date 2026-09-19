"""SDK capability / query typing surface (ADR-012)."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Protocol, runtime_checkable


@runtime_checkable
class ToolContextV2(Protocol):
    """Preferred extension facade — query / get / invoke over raw Nodes."""

    user_id: str
    workspace_id: str
    scope: str
    bundle_slug: str

    async def get(self, object_ref: dict) -> Optional[dict]: ...

    async def query(self, query_spec: dict) -> dict: ...

    async def invoke(self, operation_key: str, payload: Optional[dict] = None) -> dict: ...


# Re-export shape aliases for App authors documenting contracts
QueryMode = Literal["declared_capability", "core_open"]
