"""Mission control aggregate endpoint.

Collapses cross-workspace dashboard fan-out into one backend round-trip.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

from fastapi import Request
from jvspatial.api import endpoint

from app.api.errors import MissingAuthenticationError
from app.api.utils import attach_author_exports, export_node, resolve_principal_id
from app.services.permissions import (
    _collect_entries_for_tracks,
    get_user_accessible_apps,
    get_user_accessible_tracks,
)
from app.services.workspace_permissions import list_accessible_workspaces
from app.utils.time import utc_now


@endpoint("/me/mission-control", methods=["GET"], auth=True, tags=["MissionControl"])
async def get_mission_control_snapshot(
    request: Request,
    preview_limit: int = 50,
) -> Dict[str, Any]:
    """Return dashboard snapshot for Mission Control in one call."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    preview_limit = max(1, min(int(preview_limit), 200))

    # These three aggregates are independent (all keyed only on user_id) and each
    # makes its own DB fan-out, so run them concurrently rather than serially —
    # the snapshot's wall-time becomes max(parts) instead of sum(parts).
    workspaces, apps, tracks = await asyncio.gather(
        list_accessible_workspaces(user_id),
        get_user_accessible_apps(user_id),
        get_user_accessible_tracks(user_id),
    )
    # Reuse the tracks we just resolved to gather entries, instead of calling
    # get_user_accessible_entries(workspace_id=None) — which re-runs the single
    # most expensive step (get_user_accessible_tracks) a second time. Every track
    # here is already access-verified, so entries under them need no per-entry
    # re-check; _collect_entries_for_tracks is two indexed $in finds.
    entries = await _collect_entries_for_tracks(
        user_id, [t.id for t in tracks], include_author_entries=True
    )
    entries.sort(key=lambda e: e.updated_at or e.created_at or "", reverse=True)

    tracks_by_id = {t.id: t for t in tracks}
    preview_entries = entries[:preview_limit]
    today_start = utc_now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_iso = today_start.isoformat()

    active_track_ids = {e.track_id for e in entries if getattr(e, "track_id", None)}
    entries_today = sum(1 for e in entries if (e.created_at or "") >= today_iso)

    # Serialize all four node collections concurrently instead of in four
    # sequential comprehensions.
    exported_entries, exported_workspaces, exported_apps, exported_tracks = (
        await asyncio.gather(
            asyncio.gather(*(export_node(e) for e in preview_entries)),
            asyncio.gather(*(export_node(w) for w in workspaces)),
            asyncio.gather(*(export_node(a) for a in apps)),
            asyncio.gather(*(export_node(t) for t in tracks)),
        )
    )
    exported_entries = list(exported_entries)
    for row, e in zip(exported_entries, preview_entries):
        if not row.get("workspace_id"):
            tr = tracks_by_id.get(e.track_id or "")
            if tr is not None:
                row["workspace_id"] = getattr(tr, "workspace_id", None)
    await attach_author_exports(exported_entries)

    return {
        "workspaces": list(exported_workspaces),
        "apps": list(exported_apps),
        "tracks": list(exported_tracks),
        "preview_entries": exported_entries,
        "entries_today": entries_today,
        "active_tracks": len(active_track_ids),
    }
