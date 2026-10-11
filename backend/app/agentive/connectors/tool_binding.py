"""Bind a mounted tool to one caller-authorized connector row."""

from __future__ import annotations

from typing import Any

from app.agentive.connectors.connector_resolution import (
    ResolutionError,
    is_shared_row,
    resolve_connector_row,
)
from app.agentive.nodes import Connector
from app.services.hooks.registry import get_workspace_tools


async def resolve_mounted_tool_connector(
    *, workspace_id: str, principal_id: str, capability_key: str
) -> tuple[Any, dict[str, Any]]:
    """Resolve canonical precedence once; never accept a row from model inputs."""
    spec = get_workspace_tools(workspace_id).get(capability_key)
    if not isinstance(spec, dict) or spec.get("agent_callable") is False:
        raise ResolutionError("Connector tool is unavailable", "capability.revoked")
    slug = str(spec.get("_mcp_connector_slug") or "")
    if slug:
        row = await resolve_connector_row(
            workspace_id=workspace_id, slug=slug, principal_id=principal_id
        )
    else:
        row = await Connector.get(str(spec.get("_mcp_connector_id") or ""))
    if (
        row is None
        or not principal_id
        or not workspace_id
        or getattr(row, "workspace_id", "") != workspace_id
        or not (getattr(row, "owner", "") == principal_id or is_shared_row(row))
        or not spec.get("_mcp_remote_name")
    ):
        raise ResolutionError("Connector tool is unavailable", "capability.revoked")
    return row, spec
