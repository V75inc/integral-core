"""Adapters the capability broker calls after the snapshot gate."""

from __future__ import annotations

from typing import Any, Dict

from jvspatial.api.exceptions import JVSpatialAPIException

from app.schemas.capability_broker import CapabilityInvocation


async def dispatch_capability(inv: CapabilityInvocation, cap: Dict[str, Any]) -> Any:
    """Run the existing Core, App, or connector implementation."""
    try:
        if inv.source == "app":
            from app.services.app_operations.dispatch import invoke_app_operation

            return await invoke_app_operation(
                user_id=inv.principal_id,
                workspace_id=inv.workspace_id,
                app_id=str(inv.app_id or ""),
                operation_key=inv.capability_key,
                payload=dict(inv.arguments or {}),
                idempotency_key=inv.idempotency_key,
            )
        from app.agentive.tooling.dispatch import dispatch_tool

        result = await dispatch_tool(
            inv.capability_key,
            dict(inv.arguments or {}),
            principal_id=inv.principal_id,
            scope=inv.workspace_id,
            session_id=inv.session_id,
            interaction_id=inv.interaction_id,
            skill_tools_required=inv.skill_tools_required,
        )
        if result.is_error:
            raise AdapterError(result.error_code or "error", result.message)
        return result.data
    except AdapterError:
        raise
    except JVSpatialAPIException as exc:
        raise AdapterError(exc.error_code, exc.message) from exc


class AdapterError(Exception):
    """Normalized adapter failure the broker turns into a failed receipt."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(error_code, message)
        self.error_code = error_code
        self.message = message
