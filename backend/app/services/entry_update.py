"""Shared typed entry update for HTTP, approvals and connectors.

Policy is a caller boundary. Validation finishes before mutations, and the
observed revision is fenced before reconciling graph edges inside the same
transaction. Connector actor identity is separate from its authorized owner.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from app.api.errors import (
    InsufficientPermissionsError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from app.contracts.information import schema_revision_from_profile_version
from app.models.edges import IS_OF_TYPE, TAGGED_WITH
from app.models.nodes import Entry, EntryType, Tag, Track
from app.schemas.provenance import Provenance
from app.services.app_invariant_guards import enforce_protected_field_write
from app.services.change_event import emit_change_event
from app.services.content_moderation import validate_no_profanity
from app.services.entry_write_scope import entry_write_scope
from app.services.hooks.entry_save_runtime import run_entry_save_hooks
from app.services.migration_write_guard import assert_track_schema_writable
from app.services.operational_model_compile import slug_manifest_key
from app.services.operational_model_runtime import (
    resolve_entry_type_spec,
    resolve_track_runtime_profile,
    sync_relation_edges,
    transition_custom_fields_on_type_change,
    validate_and_materialize_entry_custom_fields,
    validate_tags_apply_to_entry_type,
    validate_taxonomy_constraints,
)
from app.utils.time import utc_now_iso


async def update_entry_in_track(
    *,
    entry_id: str,
    user_id: str,
    workspace_id: str = "",
    actor_kind: str = "human",
    actor_id: Optional[str] = None,
    title: Optional[str] = None,
    body: Optional[str] = None,
    description: Optional[str] = None,
    attachment_ids: Optional[List[str]] = None,
    custom_fields: Optional[Dict[str, Any]] = None,
    status: Optional[str] = None,
    type_id: Optional[str] = None,
    tags: Optional[List[str]] = None,
    expected_record_revision: Optional[int] = None,
    expected_schema_revision: Optional[int] = None,
    replace_custom_fields: bool = False,
    provenance: Optional[Provenance] = None,
    idempotency_key: Optional[str] = None,
    change_event_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Entry:
    """Validate and update a caller-authorized record at its observed revision."""
    outbox_id = None
    async with entry_write_scope(entry_id) as graph:
        entry = await Entry.get(entry_id)
        if entry is None:
            raise ResourceNotFoundError(message="Entry not found")
        track = await Track.get(entry.track_id)
        if track is None:
            raise ResourceNotFoundError(message="Track not found for entry")
        if workspace_id and track.workspace_id != workspace_id:
            raise InsufficientPermissionsError(
                message="Entry is outside the bound workspace"
            )
        current_revision = int(entry.record_revision or 1)
        if (
            expected_record_revision is not None
            and expected_record_revision != current_revision
        ):
            raise ResourceConflictError(
                message="Entry has changed since it was read",
                details={
                    "error_code": "record_revision_conflict",
                    "expected_record_revision": expected_record_revision,
                    "current_record_revision": current_revision,
                },
            )
        await assert_track_schema_writable(track)
        model, tier, _ = await resolve_track_runtime_profile(track)
        schema_revision = schema_revision_from_profile_version(
            getattr(model, "version_number", None)
        )
        if (
            expected_schema_revision is not None
            and expected_schema_revision != schema_revision
        ):
            raise ResourceConflictError(
                message="Entry schema has changed since it was read",
                details={
                    "error_code": "schema_revision_conflict",
                    "expected_schema_revision": expected_schema_revision,
                    "current_schema_revision": schema_revision,
                },
            )
        old_type = await EntryType.get(entry.type_id) if entry.type_id else None
        if old_type is None:
            raise ResourceNotFoundError(message="Entry type not found for entry")
        entry_type = await EntryType.get(type_id) if type_id is not None else old_type
        if entry_type is None:
            raise ResourceNotFoundError(message="Entry type not found")
        if entry_type.track_id != track.id:
            raise InsufficientPermissionsError(
                message="Entry type does not belong to this track"
            )
        type_changed = entry_type.id != old_type.id
        before = await entry.export(flat=True)
        for value, label in ((title, "title"), (body, "body"), (description, "body")):
            if value is not None:
                validate_no_profanity(value, label)

        # A type change cannot evade protections on the source schema.
        if type_changed:
            await enforce_protected_field_write(
                workspace_id=track.workspace_id,
                entry_type_key=slug_manifest_key(
                    str(
                        (old_type.form_schema or {}).get("_manifest_entry_type_key")
                        or old_type.name
                    )
                ),
                proposed_custom_fields=entry.custom_fields,
            )
        validated = None
        relations: List[Dict[str, Any]] = []
        if custom_fields is not None or type_changed:
            await enforce_protected_field_write(
                workspace_id=track.workspace_id,
                entry_type_key=slug_manifest_key(
                    str(
                        (entry_type.form_schema or {}).get("_manifest_entry_type_key")
                        or entry_type.name
                    )
                ),
                proposed_custom_fields=custom_fields,
            )
            merged = (
                dict(custom_fields or {})
                if replace_custom_fields
                else {**(entry.custom_fields or {}), **(custom_fields or {})}
            )
            if type_changed:
                merged = transition_custom_fields_on_type_change(
                    merged,
                    old_entry_type=old_type,
                    new_entry_type=entry_type,
                    runtime_tier=tier,
                )
            validated, relations = await validate_and_materialize_entry_custom_fields(
                track=track,
                entry_type=entry_type,
                custom_fields=merged,
                runtime_tier=tier,
                entry=entry,
                actor_user_id=user_id,
                actor_kind=actor_kind,
                source_entry_title=title if title is not None else entry.title,
            )
        new_tags = list(dict.fromkeys(tags)) if tags is not None else None
        if new_tags is not None:
            await validate_taxonomy_constraints(
                track_id=track.id,
                tag_ids=new_tags,
                entry_type_spec=resolve_entry_type_spec(entry_type, tier),
                runtime_tier=tier,
            )
            await validate_tags_apply_to_entry_type(
                track_id=track.id, tag_ids=new_tags, entry_type=entry_type
            )

        # Native CAS holds this row until the surrounding graph transaction
        # commits. A stale HTTP/connector snapshot cannot overwrite its peer.
        saved = await graph.database.find_one_and_update(
            entry.get_collection_name(),
            {"id": entry.id, "context.record_revision": current_revision},
            {"$set": {"context.record_revision": current_revision + 1}},
        )
        if saved is None:
            raise ResourceConflictError(
                message="Entry has changed since it was read",
                details={"error_code": "record_revision_conflict"},
            )
        if type_changed:
            for edge in await graph.find_edges_between(
                entry.id, old_type.id, edge_class=IS_OF_TYPE
            ):
                await edge.delete()
            entry.type_id = entry_type.id
            await entry.connect(entry_type, edge=IS_OF_TYPE, assigned_at=utc_now_iso())
        if title is not None:
            entry.title = title
        if body is not None:
            entry.body = body
        if description is not None:
            entry.body = description
        if attachment_ids is not None:
            entry.attachment_ids = attachment_ids
        if validated is not None:
            entry.custom_fields = validated
            await sync_relation_edges(source_entry=entry, relation_refs=relations)
            await run_entry_save_hooks(
                entry=entry,
                workspace_id=track.workspace_id,
                actor_id=user_id,
                hook_point="entry.update",
            )
        if new_tags is not None:
            for tid in set(entry.tags or []) - set(new_tags):
                for edge in await graph.find_edges_between(
                    entry.id, tid, edge_class=TAGGED_WITH
                ):
                    await edge.delete()
            for tid in set(new_tags) - set(entry.tags or []):
                tag = await Tag.get(tid)
                if tag:
                    await entry.connect(
                        tag,
                        edge=TAGGED_WITH,
                        tagged_at=utc_now_iso(),
                        tagged_by=user_id,
                    )
            entry.tags = new_tags
        if status is not None:
            entry.status = status
        if provenance is not None:
            entry.provenance = provenance
        if idempotency_key is not None:
            entry.idempotency_key = idempotency_key
        entry.record_revision = current_revision + 1
        entry.schema_revision = schema_revision
        entry.updated_at = utc_now_iso()
        if provenance is not None and provenance.source == "connector":
            entry.provenance = provenance.model_copy(
                update={"synced_at": datetime.fromisoformat(entry.updated_at)}
            )
        await entry.save()
        event = {
            "actor_kind": actor_kind,
            "actor_id": actor_id or user_id,
            "action": "entry.update",
            "resource_type": "Entry",
            "resource_id": entry.id,
            "before": before,
            "after": await entry.export(flat=True),
            "scope": f"track:{track.id}",
        }
        if change_event_sink is not None:
            change_event_sink(event)
        else:
            from jvspatial.db.postgres import PostgresTransaction

            if isinstance(graph.database, PostgresTransaction):
                from app.services.app_operations.event_outbox import insert_entry_event

                outbox_id = await insert_entry_event(
                    transaction=graph.database,
                    workspace_id=track.workspace_id,
                    event=event,
                )
    if change_event_sink is None:
        if outbox_id:
            from app.services.app_operations.event_outbox import deliver_operation_event

            # Delivery failure leaves a durable pending fact for the sweep.
            try:
                await deliver_operation_event(outbox_id=outbox_id)
            except Exception:
                import logging

                logging.getLogger(__name__).exception(
                    "Entry change event awaits recovery"
                )
        else:
            await emit_change_event(**event)
    return entry
