"""Shared entry-create contract for HTTP and service callers (transform, etc.).

Extracted from ``api/entries.py::create_entry`` so transform / internal writers
run the same validate → materialize → graph wire → hooks → ChangeEvent path
without importing the HTTP layer (I-CRUD-01).
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from app.api.errors import ResourceNotFoundError
from app.contracts.information import schema_revision_from_profile_version
from app.models.edges import AUTHORED_BY, CONTAINS, IS_OF_TYPE, TAGGED_WITH
from app.models.nodes import Entry, EntryType, Tag, Track
from app.services.app_graph import ensure_track_attached_content_profile
from app.services.change_event import emit_change_event
from app.services.content_moderation import validate_no_profanity
from app.services.content_profile_runtime import (
    resolve_entry_type_spec,
    resolve_track_runtime_profile,
    sync_relation_edges,
    validate_and_materialize_entry_custom_fields,
    validate_tags_apply_to_entry_type,
    validate_taxonomy_constraints,
)
from app.services.entry_type_service import materialize_entry_types_from_tier
from app.services.hooks.entry_save_runtime import run_entry_save_hooks
from app.services.permissions import get_user_node
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)


async def create_entry_in_track(
    *,
    track: Track,
    user_id: str,
    title: str = "",
    body: str = "",
    custom_fields: Optional[Dict[str, Any]] = None,
    tags: Optional[List[str]] = None,
    entry_type: Optional[EntryType] = None,
    type_id: str = "",
    attachment_ids: Optional[List[str]] = None,
    workspace_id: str = "",
    actor_kind: str = "human",
    skip_profanity: bool = False,
    change_event_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Entry:
    """Create an Entry on ``track`` with the full graph + materialize contract.

    Caller is responsible for ``entry.create`` policy. Does not emit watcher
    notifications or mention fan-out (those stay HTTP-layer concerns).
    """
    track_id = track.id
    resolved_type = entry_type

    if resolved_type is None and type_id:
        resolved_type = await EntryType.get(type_id)
        if resolved_type is None:
            # Anchored tracks may only have types in the manifest tier until
            # first materialization — try once, then fail closed on the
            # caller's explicit id (never silently swap to another type).
            await materialize_entry_types_from_tier(track)
            resolved_type = await EntryType.get(type_id)
        if resolved_type is None:
            raise ResourceNotFoundError(message="Entry type not found")

    if resolved_type is None:
        found = await EntryType.find({"context.track_id": track_id})
        if not found:
            await ensure_track_attached_content_profile(track)
            found = await EntryType.find({"context.track_id": track_id})
        if not found:
            found = await materialize_entry_types_from_tier(track)
        if found:
            prefer = next(
                (t for t in found if str(getattr(t, "name", "")).lower() == "post"),
                None,
            )
            resolved_type = prefer or found[0]
    if resolved_type is None:
        raise ResourceNotFoundError(message="Entry type not found")
    if resolved_type.track_id and resolved_type.track_id != track_id:
        raise ResourceNotFoundError(message="Entry type not found on track")

    resolved_type_id = resolved_type.id
    content_profile, runtime_tier, _ = await resolve_track_runtime_profile(track)
    schema_revision = schema_revision_from_profile_version(
        getattr(content_profile, "version_number", None)
    )
    (
        validated_custom_fields,
        relation_refs,
    ) = await validate_and_materialize_entry_custom_fields(
        track=track,
        entry_type=resolved_type,
        custom_fields=custom_fields or {},
        runtime_tier=runtime_tier,
        actor_user_id=user_id,
        actor_kind=actor_kind,
        source_entry_title=title,
    )
    entry_type_spec = resolve_entry_type_spec(resolved_type, runtime_tier)
    tag_ids = list(tags or [])
    await validate_taxonomy_constraints(
        track_id=track_id,
        tag_ids=tag_ids,
        entry_type_spec=entry_type_spec,
        runtime_tier=runtime_tier,
    )
    await validate_tags_apply_to_entry_type(
        track_id=track_id,
        tag_ids=tag_ids,
        entry_type=resolved_type,
    )

    if not skip_profanity:
        validate_no_profanity(title, "title")
        validate_no_profanity(body, "body")

    now = utc_now_iso()
    entry = await Entry.create(
        type_id=resolved_type_id,
        title=title,
        author_id=user_id,
        track_id=track_id,
        tags=tag_ids,
        custom_fields=validated_custom_fields,
        status="active",
        body=body,
        attachment_ids=attachment_ids or [],
        created_at=now,
        updated_at=now,
        schema_revision=schema_revision,
    )

    try:
        await track.connect(entry, edge=CONTAINS, added_at=now)
    except Exception:
        logger.exception(
            "create_entry_in_track: CONTAINS wire failed for track=%s entry=%s; "
            "rolling back the orphaned Entry",
            track_id,
            entry.id,
        )
        try:
            await entry.delete()
        except Exception:
            logger.exception(
                "create_entry_in_track: rollback delete failed for entry=%s",
                entry.id,
            )
        raise

    user = await get_user_node(user_id)
    if user:
        await entry.connect(user, edge=AUTHORED_BY, authored_at=now)

    if resolved_type_id:
        await entry.connect(resolved_type, edge=IS_OF_TYPE, assigned_at=now)

    for tag_id in tag_ids:
        tag = await Tag.get(tag_id)
        if tag:
            await entry.connect(tag, edge=TAGGED_WITH, tagged_at=now, tagged_by=user_id)

    await sync_relation_edges(source_entry=entry, relation_refs=relation_refs)
    await run_entry_save_hooks(
        entry=entry,
        workspace_id=workspace_id or getattr(track, "workspace_id", "") or "",
        actor_id=user_id,
        hook_point="entry.create",
    )

    event = {
        "actor_kind": actor_kind,
        "actor_id": user_id,
        "action": "entry.create",
        "resource_type": "Entry",
        "resource_id": entry.id,
        "before": None,
        "after": await entry.export(flat=True),
        "scope": f"track:{track.id}",
    }
    if change_event_sink is not None:
        change_event_sink(event)
    else:
        await emit_change_event(
            actor_kind=actor_kind,  # type: ignore[arg-type]
            actor_id=user_id,
            action="entry.create",
            resource_type="Entry",
            resource_id=entry.id,
            before=None,
            after=event["after"],
            scope=f"track:{track.id}",
        )
    return entry
