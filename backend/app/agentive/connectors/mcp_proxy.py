"""MCP remote-tool proxy — handler_ref target for workspace-registered tools.

Every discovered remote tool registers with
``handler_ref="app.agentive.connectors.mcp_proxy:invoke"``. The workspace
tool dispatcher (``run_tool``) and the resident overlay both call this.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.agentive.connectors.mcp_client import (
    HEALTH_ERROR,
    HEALTH_OK,
    call_remote_tool,
    describe_mcp_failure,
)
from app.schemas.policy import Resource, Subject
from app.services.change_event import emit_change_event
from app.services.hooks.registry import ToolContext
from app.services.policy_engine import evaluate as policy_evaluate

logger = logging.getLogger(__name__)


class McpProxyError(Exception):
    """Raised when an MCP proxy invoke is denied or the remote call fails."""

    def __init__(self, message: str, error_code: str = "mcp_proxy_error"):
        super().__init__(message, error_code)
        self.message = message
        self.error_code = error_code


async def _load_connector(connector_id: str) -> Any:
    from app.agentive.nodes import Connector

    c = await Connector.get(connector_id)
    if c is None:
        raise McpProxyError(
            f"MCP connector {connector_id!r} not found",
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

    The caller gate always evaluates the **human principal**. ``actor_kind`` is
    audit provenance ("did a human or the resident drive this call"), not a
    policy subject: the resident acts on behalf of ``principal_id`` and has no
    ``AgentConfig`` node of its own under that id. Passing ``kind=actor_kind``
    here sent ``Subject(kind="agent", id=<human user id>)`` into the graph-local
    branch of ``policy_evaluate``, which found no ``HAS_POLICY`` edge and
    fail-closed on every resident-initiated ``mcp__*`` call. The connector-side
    grant below is the control that constrains the agent path.
    """
    if not principal_id:
        raise McpProxyError(
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
        raise McpProxyError(
            f"tool.invoke denied for caller: {caller.reason}",
            "policy_denied",
        )
    connector_subj = await policy_evaluate(
        subject=Subject(kind="connector", id=connector_id),
        action="tool.invoke",
        resource=resource,
    )
    if not connector_subj.allowed:
        raise McpProxyError(
            f"tool.invoke denied for connector: {connector_subj.reason}",
            "policy_denied",
        )


async def _record_invoke_health(
    connector: Any, *, status: str, error: Optional[str]
) -> None:
    """Reflect the outcome of a real tool call in the connector's health.

    ``_set_health`` was only ever called from discover / mount / oauth, so a
    connector whose every invocation failed still reported ``ok`` — the status
    described whether we could once list its tools, not whether it works. The
    operator's only signal was a toast on a call they had to make themselves.

    Writes only on a transition, so the hot path does not persist a node per
    tool call. Best-effort: health bookkeeping must never fail a tool.
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
        logger.debug("mcp_proxy: health update failed", exc_info=True)


async def invoke(
    payload: Dict[str, Any],
    ctx: ToolContext,
    *,
    _mcp_connector_id: Optional[str] = None,
    _mcp_remote_name: Optional[str] = None,
    _mcp_connector_slug: Optional[str] = None,
    **_extra: Any,
) -> Any:
    """Proxy a workspace-registered MCP tool call to the remote server.

    When called from ``run_tool``, metadata arrives on the tool *spec*, not
    as kwargs — so :func:`invoke_from_spec` is the preferred entry. This
    function remains the ``handler_ref`` target and reads metadata from
    ``payload`` keys when present, else from explicit kwargs (resident overlay).

    Canonical (slug-addressed) specs carry no row id: the row is resolved
    per call from (workspace, slug, caller) — personal row wins, else the
    shared row — so one tool key serves every member correctly.
    """
    connector_id = (
        _mcp_connector_id or str(payload.pop("_mcp_connector_id", "") or "") or ""
    )
    remote_name = (
        _mcp_remote_name or str(payload.pop("_mcp_remote_name", "") or "") or ""
    )
    if not connector_id:
        slug = _mcp_connector_slug or str(payload.pop("_mcp_connector_slug", "") or "")
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
                raise McpProxyError(exc.message, exc.error_code) from None
            connector_id = resolved.id
    if not connector_id or not remote_name:
        raise McpProxyError(
            "MCP proxy requires _mcp_connector_id and _mcp_remote_name",
            "misconfigured",
        )

    await _enforce_invoke_policy(
        connector_id=connector_id,
        principal_id=ctx.user_id,
        actor_kind=getattr(ctx, "actor_kind", None) or "human",
    )

    connector = await _load_connector(connector_id)
    auth_state = dict(getattr(connector, "auth_state", None) or {})

    try:
        result = await call_remote_tool(auth_state, remote_name, payload)
    except Exception as exc:  # noqa: BLE001
        detail = describe_mcp_failure(exc)
        logger.warning(
            "mcp_proxy: remote call failed connector=%s tool=%s: %s",
            connector_id,
            remote_name,
            detail,
        )
        await _record_invoke_health(connector, status=HEALTH_ERROR, error=detail)
        raise McpProxyError(
            f"remote MCP call failed: {detail}",
            "remote_error",
        ) from exc
    await _record_invoke_health(connector, status=HEALTH_OK, error=None)

    try:
        await emit_change_event(
            actor_kind=getattr(ctx, "actor_kind", None) or "human",
            actor_id=ctx.user_id or "system",
            action="tool.invoke",
            resource_type="Connector",
            resource_id=connector_id,
            before=None,
            after={"tool": remote_name},
            scope=f"connector:{connector_id}",
            details={"remote_tool": remote_name, "via": "mcp_proxy"},
        )
    except Exception:  # noqa: BLE001 — audit must not block the tool result
        logger.exception("mcp_proxy: change-event emit failed")

    return result


async def invoke_from_spec(
    spec: Dict[str, Any],
    payload: Dict[str, Any],
    ctx: ToolContext,
) -> Any:
    """Invoke using MCP metadata stamped on the workspace tool spec."""
    return await invoke(
        dict(payload or {}),
        ctx,
        _mcp_connector_id=spec.get("_mcp_connector_id"),
        _mcp_remote_name=spec.get("_mcp_remote_name"),
        _mcp_connector_slug=spec.get("_mcp_connector_slug"),
    )
