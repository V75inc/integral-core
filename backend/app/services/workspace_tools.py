"""Agent + HTTP access to per-workspace bundle tools.

Bundle tools are registered at App install (``register_workspace_tools``) and
invoked by the UI via ``POST /api/tools/{key}``. This module is the shared
service both that route and the resident's ``integral_call_workspace_tool``
path use, so permission / agent-callable / schema checks live in one place.

I-SUBSTRATE-01: this module is domain-free. Which tools exist is whatever
the installed bundles registered; a populate-register tool vs. a
receive-shipment tool is a skill concern, not a substrate one.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    ResourceNotFoundError,
)
from app.services.hooks.registry import ToolContext, get_workspace_tools
from app.services.hooks.tool_dispatch import run_tool
from app.services.workspace_permissions import can_access_workspace

_READ_SIDE_EFFECTS = frozenset({"read", "read_only"})


def is_write_tool(spec: Dict[str, Any]) -> bool:
    """True when the compiled spec mutates substrate (must be staged)."""
    return str(spec.get("side_effects") or "read_only").strip().lower() not in (
        _READ_SIDE_EFFECTS
    )


def is_agent_callable(spec: Dict[str, Any]) -> bool:
    """False only when the bundle opted the tool out of the agent surface.

    Default True so existing tools stay reachable; action-bar-only tools
    set ``agent_callable: false``.
    """
    return spec.get("agent_callable", True) is not False


def _public_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "key": spec.get("key"),
        "name": spec.get("name"),
        "description": spec.get("description") or "",
        "parameters_schema": spec.get("parameters_schema") or {},
        "side_effects": spec.get("side_effects") or "read_only",
        "privileged": bool(spec.get("privileged")),
        "write": is_write_tool(spec),
    }


def lookup_workspace_tool(workspace_id: str, tool_key: str) -> Dict[str, Any]:
    """Return the registered spec or raise ``ResourceNotFoundError``."""
    tools = get_workspace_tools(workspace_id) if workspace_id else {}
    spec = tools.get(tool_key)
    if spec is None:
        raise ResourceNotFoundError(message=f"tool {tool_key!r} not found in workspace")
    return spec


async def list_workspace_tools(
    *,
    user_id: str,
    workspace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Catalogue of agent-callable workspace tools in the bound workspace.

    Omits ``handler_ref`` and tools with ``agent_callable: false``. Used by
    ``integral_list_workspace_tools``.
    """
    if not workspace_id:
        return {"tools": [], "reason": "no active workspace"}
    role = await can_access_workspace(user_id, workspace_id)
    if role == "none":
        raise InsufficientPermissionsError(message="Access denied")
    tools = get_workspace_tools(workspace_id)
    out = []
    for spec in tools.values():
        if not is_agent_callable(spec):
            continue
        if spec.get("privileged") and role not in ("owner", "admin"):
            continue
        out.append(_public_spec(spec))
    out.sort(key=lambda s: str(s.get("key") or ""))
    return {"tools": out, "count": len(out)}


async def invoke_workspace_tool(
    *,
    user_id: str,
    workspace_id: Optional[str] = None,
    tool_key: str,
    payload: Optional[Dict[str, Any]] = None,
    agent: bool = False,
) -> Any:
    """Run a workspace-registered bundle tool.

    ``agent=True`` (resident / MCP) additionally refuses tools the bundle
    marked ``agent_callable: false``. The UI action-bar path passes
    ``agent=False`` so a human can still click those buttons.
    """
    if not workspace_id:
        raise BadRequestError(message="no active workspace")
    key = str(tool_key or "").strip()
    if not key:
        raise BadRequestError(message="tool_key is required")
    spec = lookup_workspace_tool(workspace_id, key)
    if agent and not is_agent_callable(spec):
        raise InsufficientPermissionsError(
            message=(
                f"tool {key!r} is not callable from chat — it is wired to an "
                "in-app action button. Tell the user to click that button "
                "on the entry's page."
            )
        )
    role = await can_access_workspace(user_id, workspace_id)
    if role == "none":
        raise InsufficientPermissionsError(message="Access denied")
    if spec.get("privileged") and role not in ("owner", "admin"):
        raise InsufficientPermissionsError(
            message="Privileged tool requires workspace admin"
        )
    ctx = ToolContext(user_id=user_id, workspace_id=workspace_id, scope=f"tool:{key}")
    return await run_tool(spec, dict(payload or {}), ctx)
