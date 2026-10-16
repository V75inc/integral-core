"""Canonical tag deltas preserve Entry revisions, taxonomy and approval identity."""

from __future__ import annotations

import logging
from typing import Any

from jvspatial.core.context import get_default_context

from app.api.errors import InsufficientPermissionsError, ResourceNotFoundError
from app.models.edges import CONTAINS
from app.models.nodes import Entry, Tag
from app.services.entry_write_scope import (
    entry_write_scope,
    is_postgres_entry_transaction,
)

logger = logging.getLogger(__name__)


async def _require_tag_taxonomy(tag_id: str, location: Any) -> None:
    """Require the declared owning model and its one canonical Tag edge."""
    from app.services.attached_model_scope import read_attached_model
    from app.services.authority_facts import primary_node_record, required_node_text

    database = get_default_context().database
    row = await primary_node_record(database, Tag, tag_id)
    if row is None:
        raise ResourceNotFoundError(message="Tag not found")
    track_id = required_node_text(row, "track_id", allow_empty=True)
    app_id = required_node_text(row, "app_id", allow_empty=True)
    if row["context"].get("is_template") is not False:
        raise InsufficientPermissionsError(
            message="Tag is outside this entry's taxonomy"
        )
    if track_id == location.track_id and not app_id:
        owner_kind, owner_id = "track", track_id
    elif app_id and app_id == location.app_id and not track_id:
        owner_kind, owner_id = "app", app_id
    else:
        raise InsufficientPermissionsError(
            message="Tag is outside this entry's taxonomy"
        )
    attached = await read_attached_model(owner_kind, owner_id)
    edges = await database.find(
        "edge", {"entity": CONTAINS._entity_name(), "target": tag_id}, limit=2
    )
    if attached is None or len(edges) != 1 or edges[0].get("source") != attached[1].id:
        raise InsufficientPermissionsError(message="Tag taxonomy is unavailable")


async def change_entry_tag(
    *, user_id: str, entry_id: str, tag_id: str, present: bool
) -> dict[str, Any]:
    """Apply one exact delta under the canonical Entry command and receipt fence.

    This composes the existing typed update's taxonomy checks, revision CAS and
    edge reconciliation. Tag-only updates do not run custom-field save hooks.
    The approved command remains a tag delta; it is never relabelled as a
    model-supplied replacement of the complete tag list.
    """
    from app.agentive.services.staging_execution import staged_command_effect
    from app.services.app_operations.event_outbox import insert_entry_event
    from app.services.entry_effect_scope import entry_effect_scope
    from app.services.entry_update import update_entry_in_track
    from app.services.release_admission import release_admission
    from app.services.resource_location import DATA_APP_STATES, locate_resource

    location = await locate_resource(
        "entry", entry_id, allowed_app_states=DATA_APP_STATES
    )
    if location is None:
        raise ResourceNotFoundError(message="Entry not found")
    caller_context = get_default_context()
    kind = "add_entry_tag" if present else "remove_entry_tag"
    events: list[dict[str, Any]] = []
    outboxes: list[str] = []
    async with (
        entry_write_scope(entry_id) as graph,
        release_admission(user_id, location.workspace_id),
    ):
        current = await locate_resource(
            "entry", entry_id, allowed_app_states=DATA_APP_STATES
        )
        if current != location:
            raise InsufficientPermissionsError(message="Entry scope changed")
        # Serialize delta computation, so a concurrent tag or field change
        # cannot be overwritten by a stale read-modify-write tag list.
        row = await graph.database.find_one_and_update(
            "node", {"id": entry_id, "entity": Entry._entity_name()}, {"$set": {}}
        )
        if row is None:
            raise ResourceNotFoundError(message="Entry not found")
        entry = await graph._deserialize_entity(Entry, row)
        if entry is None:
            raise ResourceNotFoundError(message="Entry not found")
        next_tags = list(dict.fromkeys(entry.tags or []))
        if present and tag_id not in next_tags:
            next_tags.append(tag_id)
        elif not present:
            next_tags = [value for value in next_tags if value != tag_id]
        async with (
            entry_effect_scope(
                user_id=user_id,
                actor_kind="human",
                actor_id=None,
                action="entry.update",
                target_kind="entry",
                target_id=entry_id,
                workspace_id=location.workspace_id,
                payload={"entry_id": entry_id, "tags": next_tags},
            ),
            staged_command_effect(
                user_id=user_id,
                workspace_id=location.workspace_id,
                kind=kind,
                targets={"entry_id": entry_id, "tag_id": tag_id},
            ) as complete,
        ):
            await _require_tag_taxonomy(tag_id, location)
            updated = await update_entry_in_track(
                entry_id=entry_id,
                user_id=user_id,
                workspace_id=location.workspace_id,
                tags=next_tags,
                expected_record_revision=int(entry.record_revision or 1),
                change_event_sink=events.append,
            )
            if is_postgres_entry_transaction(graph.database):
                for event in events:
                    outboxes.append(
                        await insert_entry_event(
                            transaction=graph.database,
                            workspace_id=location.workspace_id,
                            event=event,
                        )
                    )
            if complete is not None:
                await complete(
                    {
                        "entry_id": entry_id,
                        "track_id": location.track_id,
                        "record_revision": updated.record_revision,
                    }
                )
    if is_postgres_entry_transaction(graph.database):
        if graph is not caller_context:
            from app.services.app_operations.event_outbox import deliver_operation_event

            for outbox in outboxes:
                try:
                    await deliver_operation_event(outbox_id=outbox)
                except Exception:
                    logger.exception("Entry tag event awaits recovery")
    else:
        from app.services.change_event import emit_change_event

        for event in events:
            await emit_change_event(**event)
    return {
        "message": "Tag added to entry" if present else "Tag removed from entry",
        "entry_id": entry_id,
        "tag_id": tag_id,
    }
