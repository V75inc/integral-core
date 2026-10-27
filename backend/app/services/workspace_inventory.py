"""Content-free workspace inventory through the canonical read audience.

This returns one count, never Entry rows or per-App domain aggregates. Reading
installed-App fields still requires the App's declared query contract.
"""

from __future__ import annotations

from typing import Any

from app.api.errors import InsufficientPermissionsError
from app.services.permissions import (
    count_user_accessible_entries_for_tracks,
    get_user_accessible_tracks,
)
from app.services.workspace_permissions import can_access_workspace
from app.utils.time import utc_now_iso


async def count_workspace_entries(
    *, user_id: str, workspace_id: str | None = None
) -> dict[str, Any]:
    """Exact inventory total, with current per-Entry policy and no content output."""
    if not workspace_id or await can_access_workspace(user_id, workspace_id) in (
        None,
        "none",
    ):
        raise InsufficientPermissionsError(
            message="Select an accessible workspace before counting its records."
        )
    tracks = [
        track
        for track in await get_user_accessible_tracks(user_id)
        if track.workspace_id == workspace_id
    ]
    counts = await count_user_accessible_entries_for_tracks(user_id, tracks)
    return {
        "total_entries": sum(counts.values()),
        "workspace_id": workspace_id,
        "audience": "entries_accessible_to_current_user",
        "coverage": "complete",
        "counted_at": utc_now_iso(),
    }
