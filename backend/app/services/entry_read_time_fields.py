"""Opt-in, non-persisted entry.precompute projections from the owning App.

Caller must authorize entry.read first. A failed projection masks the stored
value with None; no stale saved value is silently presented as current.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

from app.models.nodes import Entry, EntryType, Track
from app.services.hooks.registry import ToolContext, get_workspace_tools
from app.services.hooks.resolver import find_matching_bindings
from app.services.hooks.tool_dispatch import run_tool
from app.services.operational_model_entry_fields import resolve_entry_type_spec
from app.services.operational_model_runtime import resolve_track_runtime_profile


class ReadTimeToolContext(ToolContext):
    """Restricted facade for automatic reads; new public methods fail closed.

    Read-only declarations alone are not authority to send notifications,
    render/attach files, change settings or invoke another App's write tool.
    """

    _READ_METHODS = frozenset(
        {
            "get_entry",
            "get",
            "query",
            "find_entries",
            "find_entries_in_track_type",
            "find_track_id_by_title",
            "get_related_entries",
            "get_app_settings",
            "get_entry_system",
            "own_bundle_view",
            "track_kind",
            "read_entry_file",
            "is_workspace_administrator",
            "get_workspace_email_delivery_redacted",
            "emit_audit",
        }
    )

    def __getattribute__(self, name: str) -> Any:
        """Expose only read methods through the automatic-read facade."""
        value = super().__getattribute__(name)
        if (
            not name.startswith("_")
            and callable(value)
            and name not in self._READ_METHODS
        ):
            raise PermissionError("Method unavailable in a read-time projection")
        return value


async def resolve_entry_read_time_fields(entry: Entry, user_id: str) -> Dict[str, Any]:
    """Return values/status separately from writer-controlled custom_fields."""
    track = await Track.get(entry.track_id) if entry.track_id else None
    et = await EntryType.get(entry.type_id) if entry.type_id else None
    if track is None or et is None:
        return {}
    apps = await track.nodes(edge=["CONTAINS"], direction="in", node=["WorkspaceApp"])
    if len(apps) != 1:
        return {}
    owner = apps[0]
    slug = str(getattr(owner, "installed_package_slug", "") or "")
    if not slug or str(getattr(owner, "lifecycle_state", "active")) != "active":
        return {}
    _, runtime, _ = await resolve_track_runtime_profile(track)
    spec = resolve_entry_type_spec(et, runtime)
    field_keys = {str(f.get("key")) for f in spec.get("fields", [])}
    type_key = (
        str(spec.get("key") or et.name)
        .strip()
        .lower()
        .replace(" ", "_")
        .replace("-", "_")
    )
    bindings = find_matching_bindings(
        track.workspace_id, "entry.precompute", {"entry_type": type_key}
    )
    tools = get_workspace_tools(track.workspace_id)
    counts: Dict[str, int] = {}
    for binding in bindings:
        if binding.get("_bundle_slug") == slug:
            for key in binding.get("read_time_fields") or []:
                counts[key] = counts.get(key, 0) + 1
    values: Dict[str, Any] = {}
    statuses: Dict[str, str] = {}
    deadline = asyncio.get_running_loop().time() + 5
    for binding in bindings:
        keys = binding.get("read_time_fields") or []
        if not keys or binding.get("_bundle_slug") != slug:
            continue
        # A compiled, opt-in binding can only project declared record fields.
        keys = [k for k in keys if k in field_keys]
        if not keys:
            continue
        for key in keys:
            values[key] = None
            statuses[key] = "unavailable"
        tool = tools.get(str(binding.get("tool") or "")) or {}
        if (
            any(counts[key] != 1 for key in keys)
            or binding.get("mode") != "tool"
            or binding.get("privileged")
            or tool.get("privileged")
            or tool.get("_bundle_slug") != slug
            or tool.get("side_effects") not in ("read", "read_only")
        ):
            continue
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            continue
        ctx = ReadTimeToolContext(
            user_id=user_id,
            workspace_id=track.workspace_id,
            scope=f"entry:{entry.id}",
            read_only=True,
        )
        try:
            result = await asyncio.wait_for(
                run_tool(
                    tool,
                    {**(binding.get("tool_input") or {}), "entry_id": entry.id},
                    ctx,
                ),
                timeout=remaining,
            )
        except Exception:
            # Public status deliberately excludes tool exceptions/source data.
            continue
        for key in keys:
            if key in result:
                values[key] = result[key]
                statuses[key] = "current" if result[key] is not None else "unavailable"
    return {"values": values, "status": statuses} if statuses else {}
