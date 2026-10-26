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
        "invoke_with": "integral_call_workspace_tool",
        "input_parameter": "input",
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
    app_id: Optional[str] = None,
    tool_key: Optional[str] = None,
    read_only: bool = False,
    limit: Optional[int] = None,
    offset: int = 0,
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
    bundle_slug = None
    if app_id:
        from app.models.nodes import App
        from app.services.permissions import resolve_role

        app = await App.get(app_id)
        if (
            app is None
            or app.workspace_id != workspace_id
            or await resolve_role(user_id, "app", app_id) is None
        ):
            raise InsufficientPermissionsError(message="Access denied")
        if app.lifecycle_state != "active":
            raise ResourceNotFoundError(message="App is unavailable")
        bundle_slug = app.installed_package_slug
        if not bundle_slug:
            return {"tools": [], "count": 0, "has_more": False, "next_offset": None}
    out = []
    for spec in tools.values():
        if bundle_slug and spec.get("_bundle_slug") != bundle_slug:
            continue
        if tool_key and spec.get("key") != tool_key:
            continue
        if read_only and is_write_tool(spec):
            continue
        if not is_agent_callable(spec):
            continue
        if spec.get("privileged") and role not in ("owner", "admin"):
            continue
        out.append(_public_spec(spec))
    # Read tools lead each stable catalogue so a write-heavy App does not
    # hide its data access behind pages of action schemas. Exact-key lookup
    # and explicit read_only filtering remain available for either workflow.
    out.sort(key=lambda s: (bool(s.get("write")), str(s.get("key") or "")))
    offset = max(0, offset)
    page_size = max(1, min(limit, 50)) if limit is not None else len(out)
    page = out[offset : offset + page_size]
    more = offset + len(page) < len(out)
    return {
        "tools": page,
        "count": len(out),
        "returned_count": len(page),
        "has_more": more,
        "next_offset": offset + len(page) if more else None,
    }


async def track_read_tool_hint(
    *, user_id: str, workspace_id: str, track: Any
) -> Optional[Dict[str, Any]]:
    """Resolve a small live read catalogue after a generic App-record refusal.

    This advertises a route, never executes it or grants authority. A direct
    Track grant does not imply permission to discover its parent App's tools.
    """
    from app.services.permissions import resolve_role
    from app.services.query_boundary import parent_app_for_track

    app = await parent_app_for_track(track)
    if (
        app is None
        or app.workspace_id != workspace_id
        or await resolve_role(user_id, "app", app.id) is None
    ):
        return None
    catalogue = await list_workspace_tools(
        user_id=user_id,
        workspace_id=workspace_id,
        app_id=app.id,
        read_only=True,
        limit=5,
    )
    return {
        "tool": "integral_list_workspace_tools",
        "arguments": {"app_id": app.id, "read_only": True},
        "read_tools": catalogue["tools"],
        "has_more": catalogue["has_more"],
        "message": "Use an appropriate returned read tool through integral_call_workspace_tool. Do not repeat the generic query on this App's records.",
    }


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
    await require_workspace_tool_access(
        user_id=user_id, workspace_id=workspace_id, spec=spec
    )
    ctx = ToolContext(user_id=user_id, workspace_id=workspace_id, scope=f"tool:{key}")
    return await run_tool(spec, dict(payload or {}), ctx)


async def require_workspace_tool_access(
    *, user_id: str, workspace_id: str, spec: Dict[str, Any]
) -> None:
    """Recheck current membership and tool privilege for every dispatch path.

    Jobs retain their own execution context and may use internal tool entry
    points, but inherit the same human authority checks as a direct call.
    ``can_access_workspace`` reads fresh identity and membership facts.
    """
    role = await can_access_workspace(user_id, workspace_id)
    if role == "none":
        raise InsufficientPermissionsError(message="Access denied")
    if spec.get("privileged") and role not in ("owner", "admin"):
        raise InsufficientPermissionsError(
            message="Privileged tool requires workspace admin"
        )
