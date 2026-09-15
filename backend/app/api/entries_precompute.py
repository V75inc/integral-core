"""Generic entry.precompute endpoint (DR-30-02).

POST /api/entries/{id}/precompute  body: {hook_key: str}

Runs the bound hook (tool-mode only — declarative-mode precompute
makes no sense) against the entry, returns the tool output as a
candidate custom_fields patch (caller decides whether to PUT it).
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import Request
from jvspatial.api import endpoint

from app.api.errors import (
    InsufficientPermissionsError,
    MissingAuthenticationError,
    ResourceNotFoundError,
)
from app.api.utils import resolve_principal_id
from app.models.nodes import Entry, EntryType, Track
from app.schemas.hooks.precompute import PrecomputeRequest, PrecomputeResponse
from app.schemas.policy import Resource, Subject
from app.services.hooks.registry import ToolContext, get_workspace_tools
from app.services.hooks.resolver import find_matching_bindings
from app.services.hooks.tool_dispatch import run_tool
from app.services.policy_engine import evaluate as policy_evaluate


def _slug(s: str) -> str:
    return (s or "").strip().lower().replace(" ", "_").replace("-", "_")


@endpoint(
    "/entries/{entry_id}/precompute", methods=["POST"], auth=True, tags=["Entries"]
)
async def precompute_entry(request: Request, entry_id: str) -> Dict[str, Any]:
    """Run the bound entry.precompute tool and return its output as a custom_fields patch."""
    from app.services.hooks.errors import HookMisconfiguredError, HookNotConfiguredError

    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    raw = await request.json() if request.method == "POST" else {}
    req = PrecomputeRequest.model_validate(raw or {})

    entry = await Entry.get(entry_id)
    if entry is None:
        raise ResourceNotFoundError(message="Entry not found")

    read_decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="entry.read",
        resource=Resource(
            kind="entry",
            id=entry_id,
            scope=f"track:{entry.track_id or ''}",
        ),
    )
    if not read_decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")
    et = await EntryType.get(entry.type_id) if entry.type_id else None
    track = await Track.get(entry.track_id) if entry.track_id else None
    workspace_id = track.workspace_id if track else ""
    payload = {
        "entry_type": _slug(et.name if et else ""),
        "hook": req.hook_key,
    }
    bindings = find_matching_bindings(
        workspace_id, "entry.precompute", payload, explicit_hook_key=req.hook_key
    )
    if not bindings:
        raise HookNotConfiguredError(
            message="no entry.precompute hook matched",
            details={"payload": payload},
        )
    binding = bindings[0]
    if binding.get("mode") != "tool":
        raise HookMisconfiguredError(
            message="entry.precompute bindings must be mode=tool"
        )
    tool_key = binding.get("tool")
    tools = get_workspace_tools(workspace_id)
    spec = tools.get(tool_key)
    if spec is None:
        raise HookMisconfiguredError(
            message=f"tool {tool_key!r} not registered for workspace",
        )

    if binding.get("privileged") or spec.get("privileged"):
        from app.services.workspace_permissions import can_access_workspace

        role = await can_access_workspace(user_id, workspace_id)
        if role not in ("owner", "admin"):
            raise InsufficientPermissionsError(
                message="Privileged precompute hook requires workspace admin"
            )

    # Merge static tool_input + runtime context (entry id passed by convention).
    tool_input = {**(binding.get("tool_input") or {}), "entry_id": entry_id}
    ctx = ToolContext(
        user_id=user_id, workspace_id=workspace_id, scope=f"entry:{entry_id}"
    )
    result = await run_tool(spec, tool_input, ctx)
    return PrecomputeResponse(patch=result).model_dump()
