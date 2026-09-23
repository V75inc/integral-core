"""Native Google tool proxy — handler_ref target for workspace-registered tools.

Every native Drive / Sheets tool registers with
``handler_ref="app.agentive.connectors.native_google_proxy:invoke"`` plus
spec metadata ``_native_connector_id`` / ``_native_tool_name``. The
workspace tool dispatcher (``run_tool``) routes ``_native_connector_id``
specs through :func:`invoke_from_spec` (a bare ``handler_ref`` call cannot
see the spec) — mirroring ``mcp_proxy`` for hosted MCP tools.

Unlike the MCP proxy, the call target is first-party code
(``drive_native.call_tool`` / ``sheets_native.call_tool``) hitting the
Google REST APIs directly with the connector's OAuth tokens — no MCP
protocol involved.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.schemas.policy import Resource, Subject
from app.services.change_event import emit_change_event
from app.services.hooks.registry import ToolContext
from app.services.policy_engine import evaluate as policy_evaluate

logger = logging.getLogger(__name__)

#: Connector subclass slugs served by this proxy → their tool module.
_NATIVE_TOOL_MODULES = {
    "drive_native": "app.agentive.connectors.drive_native",
    "sheets_native": "app.agentive.connectors.sheets_native",
    "gmail": "app.agentive.connectors.gmail",
}


class NativeProxyError(Exception):
    """Raised when a native tool invoke is denied or the Google call fails."""

    def __init__(self, message: str, error_code: str = "native_proxy_error"):
        super().__init__(message, error_code)
        self.message = message
        self.error_code = error_code


async def _load_connector(connector_id: str) -> Any:
    from app.agentive.nodes import Connector

    c = await Connector.get(connector_id)
    if c is None:
        raise NativeProxyError(
            f"native connector {connector_id!r} not found",
            "connector_not_found",
        )
    return c


async def _enforce_invoke_policy(
    *,
    connector_id: str,
    principal_id: str,
    actor_kind: str = "human",
) -> None:
    """Dual gate: caller allowed + connector subject has tool.invoke (I-CON-04).

    Same contract as ``mcp_proxy._enforce_invoke_policy``: the caller gate
    always evaluates the **human principal** (the resident has no AgentConfig
    node of its own); the connector-side grant constrains the agent path.
    """
    if not principal_id:
        raise NativeProxyError(
            "tool.invoke denied: no authenticated principal",
            "policy_denied",
        )
    resource = Resource(
        kind="connector",
        id=connector_id,
        scope=f"connector:{connector_id}",
    )
    caller = await policy_evaluate(
        subject=Subject(kind="human", id=principal_id),
        action="tool.invoke",
        resource=resource,
    )
    if not caller.allowed:
        raise NativeProxyError(
            f"tool.invoke denied for caller: {caller.reason}",
            "policy_denied",
        )
    connector_subj = await policy_evaluate(
        subject=Subject(kind="connector", id=connector_id),
        action="tool.invoke",
        resource=resource,
    )
    if not connector_subj.allowed:
        raise NativeProxyError(
            f"tool.invoke denied for connector: {connector_subj.reason}",
            "policy_denied",
        )


async def _record_invoke_health(
    connector: Any, *, status: str, error: Optional[str]
) -> None:
    """Reflect the outcome of a real tool call in the connector's health.

    Writes only on a transition, so the hot path does not persist a node
    per tool call. Best-effort: health bookkeeping must never fail a tool.
    """
    try:
        current = str(getattr(connector, "health_status", "") or "")
        current_error = str(getattr(connector, "last_error", "") or "")
        if current == status and current_error == (error or ""):
            return
        from app.utils.time import utc_now_iso

        connector.health_status = status
        connector.last_error = error or None
        connector.last_health_at = utc_now_iso()
        connector.updated_at = utc_now_iso()
        await connector.save()
    except Exception:  # noqa: BLE001
        logger.debug("native_google_proxy: health update failed", exc_info=True)


async def invoke(
    payload: Dict[str, Any],
    ctx: ToolContext,
    *,
    _native_connector_id: Optional[str] = None,
    _native_tool_name: Optional[str] = None,
    _native_connector_slug: Optional[str] = None,
    **_extra: Any,
) -> Any:
    """Proxy a workspace-registered native tool call to the Google APIs.

    When called from ``run_tool``, metadata arrives on the tool *spec*, not
    as kwargs — so :func:`invoke_from_spec` is the preferred entry. This
    function remains the ``handler_ref`` target and reads metadata from
    ``payload`` keys when present, else from explicit kwargs.

    Canonical (slug-addressed) specs carry no row id: the row is resolved
    per call from (workspace, slug, caller) — personal row wins, else the
    shared row — so one tool key serves every member correctly.
    """
    connector_id = (
        _native_connector_id or str(payload.pop("_native_connector_id", "") or "") or ""
    )
    tool_name = (
        _native_tool_name or str(payload.pop("_native_tool_name", "") or "") or ""
    )
    if not connector_id:
        slug = _native_connector_slug or str(
            payload.pop("_native_connector_slug", "") or ""
        )
        if slug:
            from app.agentive.connectors.connector_resolution import (
                ResolutionError,
                resolve_connector_row,
            )

            try:
                resolved = await resolve_connector_row(
                    workspace_id=getattr(ctx, "workspace_id", "") or "",
                    slug=slug,
                    principal_id=ctx.user_id or "",
                )
            except ResolutionError as exc:
                raise NativeProxyError(exc.message, exc.error_code) from None
            connector_id = resolved.id
    if not connector_id or not tool_name:
        raise NativeProxyError(
            "native proxy requires _native_connector_id and _native_tool_name",
            "misconfigured",
        )

    await _enforce_invoke_policy(
        connector_id=connector_id,
        principal_id=ctx.user_id,
        actor_kind=getattr(ctx, "actor_kind", None) or "human",
    )

    connector = await _load_connector(connector_id)
    auth_state = dict(getattr(connector, "auth_state", None) or {})
    subclass_slug = str(getattr(connector, "subclass_slug", "") or "")
    module_path = _NATIVE_TOOL_MODULES.get(subclass_slug)
    if module_path is None:
        raise NativeProxyError(
            f"connector {connector_id!r} (slug {subclass_slug!r}) is not a "
            "native Google connector",
            "misconfigured",
        )

    import importlib

    from app.services.connectors.google_oauth import ensure_fresh_token

    try:
        ok = await ensure_fresh_token(connector)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "native_google_proxy: token refresh failed connector=%s: %s",
            connector_id,
            exc,
        )
        ok = False
    if not ok:
        raise NativeProxyError(
            "Google authorization expired — re-authorize the connector",
            "reauth_required",
        )
    access_token = (getattr(connector, "auth_state", None) or {}).get(
        "access_token"
    ) or ""

    try:
        mod = importlib.import_module(module_path)
        result = await mod.call_tool(tool_name, payload, access_token=access_token)
    except Exception as exc:  # noqa: BLE001 — NativeToolError + transport faults
        error_code = getattr(exc, "error_code", None) or "remote_error"
        detail = (
            getattr(exc, "message", None) or str(exc).strip() or "Google call failed"
        )
        logger.warning(
            "native_google_proxy: call failed connector=%s tool=%s: %s",
            connector_id,
            tool_name,
            detail,
        )
        # An expired token that slipped past the freshness check flags re-auth
        # instead of reporting a generic remote error.
        if error_code == "auth":
            try:
                auth = dict(getattr(connector, "auth_state", None) or {})
                auth["reauth_required"] = True
                connector.auth_state = auth
                await connector.save()
            except Exception:  # noqa: BLE001
                logger.debug("native_google_proxy: reauth flag failed", exc_info=True)
        await _record_invoke_health(connector, status="error", error=detail[:500])
        raise NativeProxyError(detail, error_code) from None
    await _record_invoke_health(connector, status="ok", error=None)

    try:
        await emit_change_event(
            actor_kind=getattr(ctx, "actor_kind", None) or "human",
            actor_id=ctx.user_id or "system",
            action="tool.invoke",
            resource_type="Connector",
            resource_id=connector_id,
            before=None,
            after={"tool": tool_name},
            scope=f"connector:{connector_id}",
            details={"native_tool": tool_name, "via": "native_google_proxy"},
        )
    except Exception:  # noqa: BLE001 — audit must not block the tool result
        logger.exception("native_google_proxy: change-event emit failed")

    _ = auth_state
    return result


async def invoke_from_spec(
    spec: Dict[str, Any],
    payload: Dict[str, Any],
    ctx: ToolContext,
) -> Any:
    """Invoke using native metadata stamped on the workspace tool spec."""
    return await invoke(
        dict(payload or {}),
        ctx,
        _native_connector_id=str(spec.get("_native_connector_id") or ""),
        _native_tool_name=str(spec.get("_native_tool_name") or ""),
        _native_connector_slug=str(spec.get("_native_connector_slug") or ""),
    )
