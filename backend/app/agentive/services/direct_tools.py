"""Direct-execute tool targets (no StagedChange / bless).

These run immediately via :func:`app.agentive.tooling.dispatch._dispatch_direct`.
Identity is always the dispatch ``principal_id``; args carry data only.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


async def mark_notification_read_for_dispatch(
    user_id: str,
    notification_id: str,
) -> Dict[str, Any]:
    """Mark one notification read for the acting principal."""
    from app.agentive.staging_executors import _call_endpoint
    from app.api.notifications import mark_notification_as_read

    result = await _call_endpoint(
        mark_notification_as_read,
        user_id,
        notification_id=notification_id,
    )
    if isinstance(result, dict) and result.get("error"):
        return result
    return {"ok": True, **(result or {})}


async def invoke_app_operation_for_dispatch(
    user_id: str,
    app_id: str,
    operation_key: str,
    input: Optional[Dict[str, Any]] = None,
    idempotency_key: Optional[str] = None,
    correlation_id: Optional[str] = None,
    workspace_id: str = "",
) -> Dict[str, Any]:
    """Invoke a typed app operation — MCP dispatch entry (ADR-011 / AC-04)."""
    from app.services.agent_scope import current_scope_workspace_id
    from app.services.app_operations.dispatch import invoke_app_operation

    ws = (workspace_id or current_scope_workspace_id.get() or "").strip()
    return await invoke_app_operation(
        user_id=user_id,
        workspace_id=ws,
        app_id=app_id,
        operation_key=operation_key,
        payload=dict(input or {}),
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
    )
