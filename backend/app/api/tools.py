"""POST /api/tools/{key} — direct bundle-tool invocation (DR-30-01).

Operator + MCP surface. Trust gate runs at install time so any tool
present in the workspace registry is permitted to call.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import Request
from jvspatial.api import endpoint

from app.agentive.connectors.mcp_proxy import McpProxyError
from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    MissingAuthenticationError,
    ResourceNotFoundError,
)
from app.api.utils import resolve_principal_id
from app.schemas.hooks.tool_call import ToolCallRequest, ToolCallResponse
from app.services.request_scope import resolve_workspace_id_from_request
from app.services.workspace_tools import invoke_workspace_tool


@endpoint("/tools/{tool_key}", methods=["POST"], auth=True, tags=["Tools"])
async def call_tool(request: Request, tool_key: str) -> Dict[str, Any]:
    """Invoke a workspace-registered bundle tool by key and return its output."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    workspace_id = await resolve_workspace_id_from_request(request, user_id)
    raw = await request.json() if request.method == "POST" else {}
    req = ToolCallRequest.model_validate(raw or {})

    try:
        result = await invoke_workspace_tool(
            user_id=user_id,
            workspace_id=workspace_id or "",
            tool_key=tool_key,
            payload=req.input,
            agent=False,
        )
    except McpProxyError as exc:
        # McpProxyError is a plain Exception, so an ordinary authorization
        # refusal from the MCP proxy surfaced to the caller as a 500. Map it to
        # the real shape: policy denials are 403, everything else is an
        # upstream failure, not an Integral bug.
        if exc.error_code == "policy_denied":
            raise InsufficientPermissionsError(message=exc.message) from exc
        if exc.error_code == "connector_not_found":
            raise ResourceNotFoundError(message=exc.message) from exc
        raise BadRequestError(
            message=exc.message, details={"error_code": exc.error_code}
        ) from exc
    return ToolCallResponse(output=result, tool_key=tool_key).model_dump()
