"""Stable relation identity, including bounded recovery for legacy Tracks."""

from typing import Any


async def relation_track_type(track: Any) -> str:
    """Use declared identity; recover an old ad-hoc identity from its creation.

    This does not authorize a relation or mutate a Track. Callers still check
    current containment, scope, target types and read authority. Missing retained
    creation evidence leaves the legacy title fallback in place; never guess
    an identity from the requested relation or an agent's history.
    """
    identity = track.template_id or getattr(track, "relation_type_key", "")
    if identity:
        return str(identity)
    from app.services.change_event_logger import (
        CHANGE_EVENT_LOG_LEVEL,
        get_change_event_logger,
    )

    context = get_change_event_logger()._get_log_context()
    if context is not None:
        rows = await context.database.find(
            "object",
            {
                "entity": "DBLog",
                "context.log_level": CHANGE_EVENT_LOG_LEVEL,
                "context.log_data.message": "track.create",
                "context.log_data.resource_type": "Track",
                "context.log_data.resource_id": track.id,
            },
            limit=2,
        )
        if len(rows) == 1:
            snapshot = rows[0].get("context", {}).get("log_data", {}).get("after")
            if (
                isinstance(snapshot, dict)
                and snapshot.get("id") == track.id
                and snapshot.get("workspace_id") == track.workspace_id
                and snapshot.get("created_at") == track.created_at
                and isinstance(snapshot.get("title"), str)
                and snapshot["title"].strip()
            ):
                return snapshot["title"]
    return str(track.title or "")
