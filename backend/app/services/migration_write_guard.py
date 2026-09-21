"""Single write barrier for tracks governed by an active migration."""

from __future__ import annotations

from typing import Any, Dict, List

from app.exceptions import MigrationInProgressError
from app.models.edges import CONTAINS
from app.models.nodes import App, ContentProfile, Track
from app.services.app_graph import get_track_attached_content_profile


async def assert_track_schema_writable(track: Track) -> None:
    """Reject writes while a track or parent-App migration is in progress.

    App-scope upgrades govern every child track while their per-entry runner
    catches up; track-scope publishes govern only the attached track.  This is
    deliberately a graph read, not a denormalized app-id shortcut, so the
    guard remains correct for standalone and newly attached tracks.
    """

    profiles: List[ContentProfile] = []
    track_profile = await get_track_attached_content_profile(track)
    if track_profile is not None:
        profiles.append(track_profile)
    parents = await track.nodes(edge=[CONTAINS], node=["App"], direction="in", limit=1)
    for parent in parents:
        if not isinstance(parent, App):
            continue
        profile_id = str(getattr(parent, "attached_content_profile_id", "") or "")
        if not profile_id:
            continue
        profile = await ContentProfile.get(profile_id)
        if profile is not None:
            profiles.append(profile)

    blocking: List[Dict[str, Any]] = [
        {
            "content_profile_id": profile.id,
            "scope": str(getattr(profile, "scope", "") or ""),
            "migration_status": "in_progress",
        }
        for profile in profiles
        if str(getattr(profile, "migration_status", "complete") or "complete")
        == "in_progress"
    ]
    if blocking:
        raise MigrationInProgressError(
            message="Writes are paused while this track's schema migration is in progress.",
            details={"blocking_migrations": blocking, "track_id": track.id},
        )
