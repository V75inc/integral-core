"""SDK capability / query typing surface (ADR-012)."""

from __future__ import annotations

from typing import Any, Dict, Literal, Optional, Protocol, runtime_checkable


@runtime_checkable
class ToolContextV2(Protocol):
    """Preferred extension facade — query / get / invoke over raw Nodes."""

    user_id: str
    workspace_id: str
    scope: str
    bundle_slug: str

    async def current_user_profile(self) -> Dict[str, str]:
        """Return the active principal's profile ID and bounded display name."""
        ...

    async def get(self, object_ref: dict) -> Optional[dict]: ...

    async def query(self, query_spec: dict) -> dict: ...

    async def invoke(
        self, operation_key: str, payload: Optional[dict] = None
    ) -> dict: ...

    job_id: Optional[str]

    async def enqueue_job(
        self,
        tool_key: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        idempotency_key: str,
        label: str = "",
    ) -> Dict[str, Any]: ...

    async def report_progress(
        self,
        done: Optional[int] = None,
        total: Optional[int] = None,
        label: str = "",
    ) -> bool: ...

    async def job_status(self, job_id: str) -> Optional[Dict[str, Any]]: ...


# Re-export shape aliases for App authors documenting contracts
QueryMode = Literal["declared_capability", "core_open"]
