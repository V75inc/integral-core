"""Bounded, permission-checked label resolution for agent reads."""

from __future__ import annotations

from typing import Any

from app.api.errors import BadRequestError
from app.models.nodes import Entry
from app.schemas.policy import Resource, Subject
from app.services.agent_scope import current_scope_workspace_id
from app.services.notification_paths import entry_path
from app.services.policy_engine import evaluate
from app.services.resource_location import DATA_APP_STATES, locate_resources


async def resolve_entry_labels(*, user_id: str, entry_ids: list[str]) -> dict[str, Any]:
    """Resolve labels inside the host workspace with live per-entry policy."""
    workspace_id = current_scope_workspace_id.get()
    if not workspace_id:
        raise BadRequestError(message="An active workspace is required")
    if not isinstance(entry_ids, list) or not 1 <= len(entry_ids) <= 50:
        raise BadRequestError(message="Provide between 1 and 50 entry IDs")
    if any(
        not isinstance(value, str) or not value.startswith("n.Entry.")
        for value in entry_ids
    ):
        raise BadRequestError(message="Provide valid entry IDs")
    identities = list(dict.fromkeys(entry_ids))
    locations = await locate_resources(
        "entry",
        identities,
        workspace_hints=[workspace_id],
        allowed_app_states=DATA_APP_STATES,
    )
    entries = []
    for identity in identities:
        location = locations.get(identity)
        if location is None or location.workspace_id != workspace_id:
            continue
        decision = await evaluate(
            subject=Subject(kind="human", id=user_id),
            action="entry.read",
            resource=Resource(
                kind="entry", id=identity, scope=f"track:{location.track_id}"
            ),
        )
        if not decision.allowed:
            continue
        entry = await Entry.get(identity)
        if entry is None or str(entry.track_id or "") != location.track_id:
            continue
        entries.append(
            {
                "id": identity,
                "title": str(entry.title or ""),
                "track_id": location.track_id,
                "url": entry_path(identity, location.track_id),
            }
        )
    return {"entries": entries, "unresolved_count": len(identities) - len(entries)}
