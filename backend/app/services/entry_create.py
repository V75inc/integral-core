"""Shared entry-create contract for HTTP and service callers (transform, etc.).

Extracted from ``api/entries.py::create_entry`` so transform / internal writers
run the same validate → materialize → graph wire → hooks → ChangeEvent path
without importing the HTTP layer (I-CRUD-01).
"""

from __future__ import annotations

import hashlib
import json
import logging
from copy import deepcopy
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from app.api.errors import ResourceNotFoundError
from app.contracts.information import schema_revision_from_profile_version
from app.models.edges import AUTHORED_BY, CONTAINS, IS_OF_TYPE, TAGGED_WITH
from app.models.nodes import Entry, EntryType, Tag, Track
from app.schemas.policy import Subject
from app.schemas.provenance import Provenance
from app.services.app_graph import ensure_track_attached_operational_model
from app.services.change_event import emit_change_event
from app.services.content_moderation import validate_no_profanity
from app.services.entry_type_service import materialize_entry_types_from_tier
from app.services.hooks.entry_save_runtime import (
    entry_type_hook_slug,
    run_entry_before_write_hooks,
    run_entry_save_hooks,
)
from app.services.operational_model_compile import slug_manifest_key
from app.services.operational_model_runtime import (
    resolve_entry_type_spec,
    resolve_track_runtime_profile,
    sync_relation_edges,
    validate_and_materialize_entry_custom_fields,
    validate_tags_apply_to_entry_type,
    validate_taxonomy_constraints,
)
from app.services.permissions import get_user_node
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)


async def _seed_preview_default_fields(
    *,
    track: Track,
    entry_type: EntryType,
    runtime_tier: Dict[str, Any],
    custom_fields: Dict[str, Any],
    user_id: str,
    workspace_id: str,
) -> Dict[str, Any]:
    """Fill empty fields that declare ``validation.preview_default_tool``.

    Matches compose UX (FormRegion / EntryFormExpanded): when auto-generate
    tools report a number, agent/API creates satisfy ``required`` without the
    caller supplying the value. Best-effort — tool failures leave the field
    empty so the normal required validator can still refuse.
    """
    if not workspace_id:
        return custom_fields
    spec = resolve_entry_type_spec(entry_type, runtime_tier)
    fields = list(spec.get("fields") or [])
    out = dict(custom_fields)
    for raw in fields:
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("key") or "").strip()
        validation = (
            raw.get("validation") if isinstance(raw.get("validation"), dict) else {}
        )
        tool = str(validation.get("preview_default_tool") or "").strip()
        if not key or not tool:
            continue
        if out.get(key) not in (None, ""):
            continue
        input_payload = validation.get("preview_default_input")
        payload = (
            dict(input_payload)
            if isinstance(input_payload, dict) and not isinstance(input_payload, list)
            else {}
        )
        try:
            from app.services.workspace_tools import invoke_workspace_tool

            result = await invoke_workspace_tool(
                user_id=user_id,
                workspace_id=workspace_id,
                tool_key=tool,
                payload=payload,
                agent=False,
            )
        except Exception:  # noqa: BLE001 — preview is best-effort
            logger.debug(
                "preview_default_tool %r failed for field %r on track %s",
                tool,
                key,
                track.id,
                exc_info=True,
            )
            continue
        if not isinstance(result, dict) or result.get("auto_generate") is not True:
            continue
        number = str(result.get("number") or "").strip()
        if number:
            out[key] = number
    return out


async def _create_entry_in_track(
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
    actor_id: Optional[str] = None,
    authorization_subject: Optional[Subject] = None,
    provenance: Optional[Provenance] = None,
    idempotency_key: str = "",
    skip_profanity: bool = False,
    change_event_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Entry:
    """Create an Entry on ``track`` with the full graph + materialize contract.

    Caller is responsible for ``entry.create`` policy. Does not emit watcher
    notifications or mention fan-out (those stay HTTP-layer concerns).
    """
    track_id = track.id
    from app.services.migration_write_guard import assert_track_schema_writable

    await assert_track_schema_writable(track)
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
            await ensure_track_attached_operational_model(track)
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

    # Every generic creation surface (HTTP, public sharing, imports and
    # transforms) converges here. Protected App state is writable only by a
    # declared operation, which sets the scoped operation-write context.
    from app.services.app_invariant_guards import enforce_protected_field_write

    entry_type_key = slug_manifest_key(
        str(
            (getattr(resolved_type, "form_schema", None) or {}).get(
                "_manifest_entry_type_key"
            )
            or getattr(resolved_type, "name", "")
        )
    )
    await enforce_protected_field_write(
        principal_id=(
            actor_id if actor_id and actor_kind in {"agent", "connector"} else user_id
        ),
        workspace_id=str(getattr(track, "workspace_id", "") or workspace_id),
        entry_type_key=entry_type_key,
        proposed_custom_fields=custom_fields,
        track_id=track.id,
        mutation_kind="create",
        proposed_fields={"title": title, "body": body},
    )

    resolved_type_id = resolved_type.id
    operational_model, runtime_tier, _ = await resolve_track_runtime_profile(track)
    schema_revision = schema_revision_from_profile_version(
        getattr(operational_model, "version_number", None)
    )
    seeded_custom_fields = await _seed_preview_default_fields(
        track=track,
        entry_type=resolved_type,
        runtime_tier=runtime_tier,
        custom_fields=dict(custom_fields or {}),
        user_id=user_id,
        workspace_id=str(getattr(track, "workspace_id", "") or workspace_id),
    )
    await enforce_protected_field_write(
        principal_id=(
            actor_id if actor_id and actor_kind in {"agent", "connector"} else user_id
        ),
        workspace_id=str(track.workspace_id or workspace_id),
        entry_type_key=entry_type_key,
        proposed_custom_fields=seeded_custom_fields,
        track_id=track.id,
        mutation_kind="create",
        proposed_fields={"title": title, "body": body},
    )
    (
        validated_custom_fields,
        relation_refs,
    ) = await validate_and_materialize_entry_custom_fields(
        track=track,
        entry_type=resolved_type,
        custom_fields=seeded_custom_fields,
        runtime_tier=runtime_tier,
        actor_user_id=user_id,
        actor_kind=actor_kind,
        authorization_subject=authorization_subject,
        source_entry_title=title,
    )
    ws_id = str(getattr(track, "workspace_id", "") or workspace_id)
    # Schema materialization may add protected defaults or empty fields. Those
    # are not caller writes. Snapshot deeply so hooks cannot hide nested edits
    # by mutating their input in place.
    before_hook_custom_fields = deepcopy(validated_custom_fields)
    try:
        validated_custom_fields = await run_entry_before_write_hooks(
            workspace_id=ws_id,
            actor_id=user_id,
            hook_point="entry.create",
            entry_type_slug=entry_type_hook_slug(resolved_type),
            entry_type_id=str(resolved_type.id),
            operation="create",
            custom_fields=validated_custom_fields,
            write_actor=user_id,
        )
    except ValueError as exc:
        from app.api.errors import BadRequestError

        raise BadRequestError(message=str(exc)) from exc
    post_hook_custom_fields = {
        key: validated_custom_fields.get(key)
        for key in before_hook_custom_fields.keys() | validated_custom_fields.keys()
        if key not in before_hook_custom_fields or key not in validated_custom_fields
        # JSON comparison distinguishes nested booleans/numbers while ignoring
        # object key order. Presence checks also catch added/removed nulls.
        or json.dumps(before_hook_custom_fields[key], sort_keys=True)
        != json.dumps(validated_custom_fields[key], sort_keys=True)
    }
    await enforce_protected_field_write(
        principal_id=(
            actor_id if actor_id and actor_kind in {"agent", "connector"} else user_id
        ),
        workspace_id=str(track.workspace_id or workspace_id),
        entry_type_key=entry_type_key,
        proposed_custom_fields=post_hook_custom_fields,
        track_id=track.id,
        mutation_kind="create",
        proposed_fields={"title": title, "body": body},
    )
    (
        validated_custom_fields,
        relation_refs,
    ) = await validate_and_materialize_entry_custom_fields(
        track=track,
        entry_type=resolved_type,
        custom_fields=validated_custom_fields,
        runtime_tier=runtime_tier,
        actor_user_id=user_id,
        actor_kind=actor_kind,
        authorization_subject=authorization_subject,
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
    if provenance is not None and provenance.source == "connector":
        provenance = provenance.model_copy(
            update={"synced_at": datetime.fromisoformat(now)}
        )
    attributes = dict(
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
        idempotency_key=idempotency_key,
        **({"provenance": provenance} if provenance is not None else {}),
    )
    if idempotency_key:
        # The runtime supplies a fully scoped key. The unique Node id is the
        # final insertion fence, including destination-rebinding races.
        from app.api.errors import ResourceConflictError

        entry, inserted = await Entry.create_if_absent(
            id="n.Entry." + hashlib.sha256(idempotency_key.encode()).hexdigest(),
            **attributes,
        )
        if not inserted:
            raise ResourceConflictError(
                message="Connector record identity already exists; reconcile its destination"
            )
    else:
        entry = await Entry.create(**attributes)

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
        "actor_id": actor_id or user_id,
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
            actor_id=actor_id or user_id,
            action="entry.create",
            resource_type="Entry",
            resource_id=entry.id,
            before=None,
            after=event["after"],
            scope=f"track:{track.id}",
        )
    return entry


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
    actor_id: Optional[str] = None,
    authorization_subject: Optional[Subject] = None,
    provenance: Optional[Provenance] = None,
    idempotency_key: str = "",
    skip_profanity: bool = False,
    change_event_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Entry:
    """Canonical create; graph edges and its event fact share the PG commit."""
    from jvspatial.core.context import get_default_context

    from app.services.entry_write_scope import (
        entry_write_scope,
        is_postgres_entry_transaction,
    )

    events: List[Dict[str, Any]] = []
    outbox_id = None
    caller_context = get_default_context()
    from app.services.approved_effect import (
        approved_capture_active,
        approved_event_sink,
    )

    if await approved_capture_active():
        change_event_sink = approved_event_sink
    from app.agentive.services.staging_execution import staged_entry_effect

    async with (
        entry_write_scope(f"track:{track.id}") as graph,
        staged_entry_effect(
            user_id=user_id,
            workspace_id=workspace_id or track.workspace_id,
            track_id=track.id,
        ) as complete_staged_effect,
    ):
        fresh_track = await Track.get(track.id)
        if fresh_track is None:
            raise ResourceNotFoundError(message="Track not found")
        if workspace_id and fresh_track.workspace_id != workspace_id:
            from app.api.errors import InsufficientPermissionsError

            raise InsufficientPermissionsError(
                message="Track is outside the bound workspace"
            )
        # Rehydrate the type under the command's context as well.
        fresh_type = await EntryType.get(entry_type.id) if entry_type else None
        from app.services.entry_effect_scope import entry_effect_scope

        actual_payload = {
            "track_id": fresh_track.id,
            "title": title,
            "body": body,
            "type_id": type_id or (entry_type.id if entry_type else ""),
            "custom_fields": dict(custom_fields or {}),
            "tags": list(tags or []),
            "attachment_ids": list(attachment_ids or []),
            **(
                {"provenance": provenance.model_dump(mode="json")} if provenance else {}
            ),
        }
        candidate_type = fresh_type or (
            await EntryType.find_one({"id": type_id}) if type_id else None
        )
        key = (
            slug_manifest_key(
                str(
                    (candidate_type.form_schema or {}).get("_manifest_entry_type_key")
                    or candidate_type.name
                )
            )
            if candidate_type
            else ""
        )
        async with entry_effect_scope(
            user_id=user_id,
            actor_kind=actor_kind,
            actor_id=actor_id,
            action="entry.create",
            target_kind="track",
            target_id=fresh_track.id,
            workspace_id=fresh_track.workspace_id,
            payload=actual_payload,
            entry_type_key=key,
            authorization_subject=authorization_subject,
        ) as admitted_principal_id:
            entry = await _create_entry_in_track(
                track=fresh_track,
                user_id=admitted_principal_id,
                title=title,
                body=body,
                custom_fields=custom_fields,
                tags=tags,
                entry_type=fresh_type,
                type_id=type_id or (entry_type.id if entry_type else ""),
                attachment_ids=attachment_ids,
                workspace_id=workspace_id,
                actor_kind=actor_kind,
                actor_id=actor_id,
                authorization_subject=authorization_subject,
                provenance=provenance,
                idempotency_key=idempotency_key,
                skip_profanity=skip_profanity,
                change_event_sink=change_event_sink or events.append,
            )
        if events and is_postgres_entry_transaction(graph.database):
            from app.services.app_operations.event_outbox import insert_entry_event

            outbox_id = await insert_entry_event(
                transaction=graph.database,
                workspace_id=fresh_track.workspace_id,
                event=events[0],
            )
        if complete_staged_effect is not None:
            await complete_staged_effect(
                {"entry_id": entry.id, "track_id": fresh_track.id}
            )
    # Callers may save provenance or connect a transformed record after this
    # command returns. Its owned transaction has released the connection; a
    # nested command must instead retain the caller's still-active context.
    await entry.set_context(caller_context)
    if outbox_id and graph is not caller_context:
        from app.services.app_operations.event_outbox import deliver_operation_event

        try:
            await deliver_operation_event(outbox_id=outbox_id)
        except Exception:
            logger.exception("Entry change event awaits recovery")
    elif events and not outbox_id:
        await emit_change_event(**events[0])
    return entry
