"""Manifest tool listing and execution endpoints.

The agent-facing external tool surface. ``GET /api/agentive/tools`` serves the
manifest-driven catalogue (:func:`build_tool_catalogue`); ``POST
/api/agentive/tools/{tool_name}`` dispatches a single tool by name via
:func:`dispatch_tool` under the authenticated principal + bound workspace scope.
"""

from typing import Any, Dict, Optional

from fastapi import Request
from jvspatial.api import endpoint

from app.agentive.services.tool_scope import resolve_scope_from_request
from app.agentive.tooling.catalogue import build_tool_catalogue
from app.agentive.tooling.dispatch import dispatch_tool
from app.api.errors import MissingAuthenticationError
from app.api.utils import resolve_principal_id


@endpoint("/agentive/tools", methods=["GET"], auth=True, tags=["Agentive"])
async def list_tools(request: Request) -> Dict[str, Any]:
    """List the dispatchable manifest tools available to agents."""
    catalogue = build_tool_catalogue()
    return {"tools": catalogue, "total": len(catalogue)}


@endpoint("/agentive/tools/{tool_name}", methods=["POST"], auth=True, tags=["Agentive"])
async def execute_tool_endpoint(
    request: Request,
    tool_name: str,
    parameters: Optional[Dict[str, Any]] = None,
    scope: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Dispatch a manifest tool on behalf of the authenticated agent."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    parameters = parameters or {}
    # Two scope inputs accepted:
    #   1. X-Integral-Scope header (canonical — frontend sets this on every
    #      tool call so the agent stays workspace-aware without juggling it)
    #   2. body.scope (explicit override — useful for tests / system callers)
    if scope is None:
        scope = resolve_scope_from_request(request)
    # W5: fail closed. An authenticated request with no scope context
    # defaults to the caller's Personal Workspace, NEVER to unconstrained.
    if scope is None:
        from app.services.personal_workspace import (
            ensure_personal_workspace_for_user_id,
        )

        personal = await ensure_personal_workspace_for_user_id(user_id)
        if personal is not None:
            scope = {"kind": "workspace", "workspace_id": personal.id}

    # ``dispatch_tool`` wants a workspace-id STRING (or None), not the
    # ``{"kind": "workspace", "workspace_id": ...}`` envelope this endpoint
    # historically accepted — extract the id while preserving the fail-closed
    # personal-workspace default established above.
    workspace_id: Optional[str] = scope.get("workspace_id") if scope else None

    result = await dispatch_tool(
        tool_name, parameters, principal_id=user_id, scope=workspace_id
    )
    if result.is_error:
        # Preserve the prior error contract: the failure rides inside ``result``
        # so existing callers (frontend JvAgentProvider) keep their shape.
        return {
            "tool": tool_name,
            "result": {
                "error": result.error_code or "error",
                "message": result.message,
            },
        }
    return {"tool": tool_name, "result": result.data}
