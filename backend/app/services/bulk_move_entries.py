"""Preflighted, same-workspace bulk Entry moves (W4.3)."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.exceptions import BadRequestError
from app.models.edges import (
    ANCHORS,
    CONTAINS,
    HAS_MEMBER_REF,
    IS_OF_TYPE,
    REFERENCES,
    TAGGED_WITH,
)
from app.models.nodes import Entry, EntryType, Tag, Track
from app.schemas.policy import Resource, Subject
from app.services.app_operations.transaction_scope import (
    OperationTransactionUnavailable,
    graph_transaction_available,
    postgres_graph_transaction,
)
from app.services.change_event import emit_change_event
from app.services.operational_model_compile import _slug
from app.services.operational_model_entry_fields import (
    resolve_entry_type_spec,
    validate_and_materialize_entry_custom_fields,
    validate_tags_apply_to_entry_type,
    validate_taxonomy_constraints,
)
from app.services.operational_model_runtime import resolve_track_runtime_profile
from app.services.policy_engine import evaluate as policy_evaluate
from app.utils.time import utc_now_iso

_MAX_ENTRIES = 500
logger = logging.getLogger(__name__)


def _type_key(entry_type: EntryType) -> str:
    form = getattr(entry_type, "form_schema", None) or {}
    key = form.get("_manifest_entry_type_key") if isinstance(form, dict) else None
    return _slug(str(key or entry_type.name or ""))


async def _app_id(track: Track) -> str:
    apps = await track.nodes(edge=[CONTAINS], direction="in", node=["WorkspaceApp"])
    return str(apps[0].id) if apps else ""


async def _entry_track_id(entry: Entry) -> str:
    return str(getattr(entry, "track_id", "") or "")


async def _entry_types_for_track(track: Track) -> List[EntryType]:
    """Read schema nodes from the attached model, with legacy Track fallback."""
    from app.services.app_graph import get_track_attached_operational_model

    model = await get_track_attached_operational_model(track)
    if model is not None:
        types = await model.nodes(edge=[CONTAINS], node=["EntryType"])
        if types:
            return list(types)
    return list(await track.nodes(edge=[CONTAINS], node=["EntryType"]))


async def _track_schema_state(track: Track) -> Dict[str, Any]:
    """Return the effective model version and content fingerprint for a Track."""
    from app.services.app_graph import (
        get_app_attached_operational_model,
        get_track_attached_operational_model,
    )

    models = []
    track_model = await get_track_attached_operational_model(track)
    if track_model is not None:
        models.append(track_model)
    app_id = await _app_id(track)
    if app_id:
        from app.models.nodes import App

        app = await App.get(app_id)
        app_model = await get_app_attached_operational_model(app) if app else None
        if app_model is not None:
            models.append(app_model)
    snapshots = []
    for model in models:
        entry_types = await model.nodes(edge=[CONTAINS], node=["EntryType"])
        tags = await model.nodes(edge=[CONTAINS], node=["Tag"])
        snapshots.append(
            {
                "id": model.id,
                "version_number": int(getattr(model, "version_number", 1) or 1),
                "updated_at": getattr(model, "updated_at", None),
                "manifest": getattr(model, "manifest", {}) or {},
                "entry_types": [
                    {
                        "id": item.id,
                        "key": _type_key(item),
                        "form_schema": item.form_schema,
                    }
                    for item in sorted(entry_types, key=lambda value: value.id)
                ],
                "tags": [
                    {
                        "id": item.id,
                        "track_id": item.track_id,
                        "app_id": item.app_id,
                        "name": item.name,
                        "group_key": item.group_key,
                        "parent_tag_id": item.parent_tag_id,
                        "applies_to_entry_types": item.applies_to_entry_types or [],
                    }
                    for item in sorted(tags, key=lambda value: value.id)
                ],
            }
        )
    revision = (
        snapshots[0]["version_number"]
        if snapshots
        else int(getattr(track, "schema_revision", 1) or 1)
    )
    return {
        "revision": revision,
        "fingerprint": _fingerprint(
            {"track_id": track.id, "fallback_revision": revision, "models": snapshots}
        ),
    }


def _fingerprint(value: Dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


async def _allowed(
    user_id: str, action: str, kind: str, resource_id: str, scope: str
) -> bool:
    decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action=action,
        resource=Resource(kind=kind, id=resource_id, scope=scope),
    )
    return bool(decision.allowed)


async def prepare_bulk_move(
    *,
    user_id: str,
    entry_ids: List[str],
    target_track_id: str,
    entry_type_mapping: Dict[str, str],
    field_mapping: Dict[str, Dict[str, str]],
    tag_mapping: Optional[Dict[str, str]] = None,
    allow_empty: bool = False,
    workspace_id: str = "",
) -> Dict[str, Any]:
    """Validate the full set and return a revision-bound value-free preview."""
    ids = [str(item) for item in entry_ids]
    if (
        (not ids and not allow_empty)
        or len(ids) > _MAX_ENTRIES
        or len(set(ids)) != len(ids)
    ):
        return {
            "error": "invalid_entries",
            "detail": f"entry_ids must contain 1–{_MAX_ENTRIES} unique ids",
        }
    if not isinstance(entry_type_mapping, dict) or not isinstance(field_mapping, dict):
        return {
            "error": "invalid_mapping",
            "detail": "entry_type_mapping and field_mapping must be objects",
        }
    tag_mapping = dict(tag_mapping or {})
    if any(
        not isinstance(source, str)
        or not source.strip()
        or not isinstance(destination, str)
        or not destination.strip()
        for source, destination in tag_mapping.items()
    ):
        return {
            "error": "invalid_mapping",
            "detail": "tag_mapping must map non-empty source Tag ids to destination Tag ids",
        }
    if any(
        not isinstance(source, str)
        or not source.strip()
        or not isinstance(destination, str)
        or not destination.strip()
        for source, destination in entry_type_mapping.items()
    ):
        return {
            "error": "invalid_mapping",
            "detail": "entry_type_mapping must map non-empty type keys to non-empty type keys",
        }
    if any(
        not isinstance(source_type, str)
        or not isinstance(mapping, dict)
        or any(
            not isinstance(source_field, str)
            or not source_field.strip()
            or not isinstance(target_field, str)
            or not target_field.strip()
            for source_field, target_field in mapping.items()
        )
        for source_type, mapping in field_mapping.items()
    ):
        return {
            "error": "invalid_mapping",
            "detail": "field_mapping must map type keys to non-empty source/destination field keys",
        }

    target_track = await Track.get(target_track_id)
    if target_track is None:
        return {"error": "not_found", "detail": "Target Track not found"}
    target_workspace = str(getattr(target_track, "workspace_id", "") or "")
    if not target_workspace or (workspace_id and target_workspace != workspace_id):
        return {
            "error": "workspace_mismatch",
            "detail": "Target Track is outside the active workspace",
        }
    if not await _allowed(
        user_id, "entry.create", "entry", "", f"track:{target_track.id}"
    ):
        return {
            "error": "forbidden",
            "detail": "You cannot create entries in the target Track",
        }
    from app.services.migration_write_guard import assert_track_schema_writable

    await assert_track_schema_writable(target_track)

    _, target_tier, _ = await resolve_track_runtime_profile(target_track)
    target_schema_state = await _track_schema_state(target_track)
    target_types = await _entry_types_for_track(target_track)
    target_type_by_key: Dict[str, EntryType] = {}
    for item in target_types:
        key = _type_key(item)
        if key in target_type_by_key:
            return {
                "error": "ambiguous_target_schema",
                "detail": f"Destination Track has duplicate EntryType key '{key}'",
            }
        target_type_by_key[key] = item
    entries: List[Entry] = []
    for entry_id in ids:
        entry = await Entry.get(entry_id)
        if entry is None:
            return {"error": "not_found", "detail": f"Entry not found: {entry_id}"}
        entries.append(entry)

    rows: List[Dict[str, Any]] = []
    revisions: Dict[str, int] = {}
    source_schema_revisions: Dict[str, int] = {}
    source_workspace_ids = set()
    source_schema_states: Dict[str, Dict[str, Any]] = {}
    for entry in entries:
        source_track = await Track.get(str(getattr(entry, "track_id", "") or ""))
        if source_track is None:
            return {
                "error": "source_track_missing",
                "detail": f"Entry {entry.id} has no source Track",
            }
        source_workspace = str(getattr(source_track, "workspace_id", "") or "")
        source_workspace_ids.add(source_workspace)
        if source_workspace != target_workspace or (
            workspace_id and source_workspace != workspace_id
        ):
            return {
                "error": "workspace_mismatch",
                "detail": f"Entry {entry.id} is outside the target Track workspace",
            }
        if source_track.id == target_track.id:
            return {
                "error": "already_in_target_track",
                "detail": f"Entry {entry.id} is already in the destination Track",
            }
        await assert_track_schema_writable(source_track)
        if source_track.id not in source_schema_states:
            source_schema_states[source_track.id] = await _track_schema_state(
                source_track
            )
        if not await _allowed(
            user_id, "entry.update", "entry", entry.id, f"track:{source_track.id}"
        ):
            return {"error": "forbidden", "detail": f"Entry {entry.id} is not editable"}

        old_type = await EntryType.get(str(getattr(entry, "type_id", "") or ""))
        if old_type is None:
            return {
                "error": "entry_type_missing",
                "detail": f"Entry {entry.id} has no EntryType",
            }
        if getattr(old_type, "track_id", "") and old_type.track_id != source_track.id:
            return {
                "error": "source_entry_type_mismatch",
                "detail": f"Entry {entry.id} has an EntryType outside its source Track",
            }
        source_key = _type_key(old_type)
        destination_key = _slug(str(entry_type_mapping.get(source_key) or ""))
        new_type = target_type_by_key.get(destination_key)
        if not destination_key or new_type is None:
            return {
                "error": "mapping_required",
                "detail": f"Entry {entry.id} ({source_key}) needs a valid entry_type_mapping",
            }
        per_type_field_map = field_mapping.get(source_key)
        if not isinstance(per_type_field_map, dict):
            return {
                "error": "mapping_required",
                "detail": f"EntryType '{source_key}' needs an explicit field_mapping",
            }

        source_values = dict(getattr(entry, "custom_fields", None) or {})
        mapped: Dict[str, Any] = {
            k: v for k, v in source_values.items() if str(k).startswith("_")
        }
        unmapped = []
        for key, value in source_values.items():
            if str(key).startswith("_"):
                continue
            destination_field = per_type_field_map.get(str(key))
            if not destination_field:
                unmapped.append(str(key))
                continue
            mapped[str(destination_field)] = value
        if unmapped:
            return {
                "error": "field_mapping_required",
                "detail": f"Entry {entry.id} has unmapped fields: {', '.join(sorted(unmapped))}",
            }

        mapped_targets = [
            str(value)
            for key, value in per_type_field_map.items()
            if not str(key).startswith("_")
        ]
        if len(mapped_targets) != len(set(mapped_targets)):
            return {
                "error": "invalid_mapping",
                "detail": f"EntryType '{source_key}' maps multiple fields to the same destination field",
            }
        source_tags = list(getattr(entry, "tags", None) or [])
        tags: List[str] = []
        tag_taxonomy: List[Dict[str, Any]] = []
        target_app_id = await _app_id(target_track)
        for source_tag_id in source_tags:
            mapped_tag_id = str(tag_mapping.get(str(source_tag_id)) or source_tag_id)
            tag = await Tag.get(mapped_tag_id)
            tag_scope_ok = bool(
                tag
                and (
                    str(getattr(tag, "track_id", "") or "") == target_track.id
                    or (
                        target_app_id
                        and str(getattr(tag, "app_id", "") or "") == target_app_id
                    )
                )
            )
            if tag is None or not tag_scope_ok:
                return {
                    "error": "tag_mapping_required",
                    "detail": f"Entry {entry.id} Tag {source_tag_id} needs a valid tag_mapping to the destination scope",
                }
            source_tag = await Tag.get(str(source_tag_id))
            if source_tag is None:
                return {
                    "error": "source_tag_missing",
                    "detail": f"Entry {entry.id} references missing Tag {source_tag_id}",
                }
            source_applies_to = {
                _slug(str(value)) for value in source_tag.applies_to_entry_types or []
            }
            mapped_source_applies_to = {
                _slug(
                    str(
                        entry_type_mapping.get(value)
                        or entry_type_mapping.get(_slug(value))
                        or value
                    )
                )
                for value in source_applies_to
            }
            target_applies_to = {
                _slug(str(value)) for value in tag.applies_to_entry_types or []
            }
            if (source_tag.group_key or "") != (
                tag.group_key or ""
            ) or mapped_source_applies_to != target_applies_to:
                return {
                    "error": "tag_taxonomy_mismatch",
                    "detail": f"Entry {entry.id} Tag {source_tag_id} maps to an incompatible destination Tag",
                }
            tag_taxonomy.append(
                {
                    "source_tag_id": source_tag.id,
                    "source_group_key": source_tag.group_key or "",
                    "source_applies_to_entry_types": sorted(source_applies_to),
                    "source_parent_tag_id": source_tag.parent_tag_id or "",
                    "target_tag_id": tag.id,
                    "target_group_key": tag.group_key or "",
                    "target_applies_to_entry_types": sorted(target_applies_to),
                    "target_parent_tag_id": tag.parent_tag_id or "",
                }
            )
            tags.append(mapped_tag_id)
        try:
            materialized, _ = await validate_and_materialize_entry_custom_fields(
                track=target_track,
                entry_type=new_type,
                custom_fields=mapped,
                runtime_tier=target_tier,
                entry=entry,
                actor_user_id=user_id,
            )
            spec = resolve_entry_type_spec(new_type, target_tier)
            await validate_taxonomy_constraints(
                track_id=target_track.id,
                tag_ids=tags,
                entry_type_spec=spec,
                runtime_tier=target_tier,
            )
            await validate_tags_apply_to_entry_type(
                track_id=target_track.id, tag_ids=tags, entry_type=new_type
            )
        except BadRequestError as exc:
            return {
                "error": "entry_validation_failed",
                "detail": f"Entry {entry.id} failed destination validation: {getattr(exc, 'message', str(exc))}",
            }
        rows.append(
            {
                "entry_id": entry.id,
                "source_track_id": source_track.id,
                "source_entry_type": source_key,
                "target_entry_type": destination_key,
                "mapped_field_count": len(per_type_field_map),
                "status": "ready",
                "_custom_fields": materialized,
                "_type_id": new_type.id,
                "_tag_ids": list(dict.fromkeys(tags)),
                "_source_tag_ids": source_tags,
                "_tag_taxonomy": tag_taxonomy,
            }
        )
        revisions[entry.id] = int(getattr(entry, "record_revision", 0) or 0)
        source_schema_revisions[source_track.id] = int(
            getattr(source_track, "schema_revision", 1) or 1
        )

    if len(source_workspace_ids) != 1 and not (allow_empty and not entries):
        return {
            "error": "workspace_mismatch",
            "detail": "All entries must come from the same workspace as the target Track",
        }
    public_rows = [
        {k: v for k, v in row.items() if not k.startswith("_")} for row in rows
    ]
    schema_revision = int(target_schema_state["revision"])
    digest_payload = {
        "entry_ids": ids,
        "target_track_id": target_track.id,
        "target_schema_revision": schema_revision,
        "target_schema_fingerprint": target_schema_state["fingerprint"],
        "source_schema_states": source_schema_states,
        "entry_type_mapping": entry_type_mapping,
        "field_mapping": field_mapping,
        "tag_mapping": tag_mapping,
        "record_revisions": revisions,
        "source_schema_revisions": source_schema_revisions,
        "rows": [
            {
                "entry_id": row["entry_id"],
                "source_track_id": row["source_track_id"],
                "target_type_id": row["_type_id"],
                "custom_fields": row["_custom_fields"],
                "tag_ids": row["_tag_ids"],
                "tag_taxonomy": row["_tag_taxonomy"],
            }
            for row in rows
        ],
    }
    return {
        "target_track_id": target_track.id,
        "target_workspace_id": target_workspace,
        "target_schema_revision": schema_revision,
        "target_schema_fingerprint": target_schema_state["fingerprint"],
        "record_revisions": revisions,
        "source_schema_states": source_schema_states,
        "preview_fingerprint": _fingerprint(digest_payload),
        "entries": public_rows,
        "_rows": rows,
        "_target_track": target_track,
    }


async def move_entries(
    *,
    user_id: str,
    payload: Dict[str, Any],
    workspace_id: str = "",
    after_move: Optional[Callable[[], Awaitable[None]]] = None,
    allow_empty: bool = False,
    emit_entry_audits: bool = True,
    _transaction_internal: bool = False,
) -> Dict[str, Any]:
    """Revalidate, then atomically reparent every entry or write nothing."""
    if not _transaction_internal and not graph_transaction_available():
        return {
            "error": True,
            "error_code": "transaction_unavailable",
            "message": "Bulk moves require a store that supports graph transactions",
        }
    prepared = await prepare_bulk_move(
        user_id=user_id,
        entry_ids=list(payload.get("entry_ids") or []),
        target_track_id=str(payload.get("target_track_id") or ""),
        entry_type_mapping=payload.get("entry_type_mapping"),
        field_mapping=payload.get("field_mapping"),
        tag_mapping=payload.get("tag_mapping"),
        allow_empty=allow_empty,
        workspace_id=workspace_id,
    )
    if prepared.get("error"):
        return {
            "error": True,
            "error_code": prepared["error"],
            "message": prepared["detail"],
        }
    if not _transaction_internal and (
        prepared.get("preview_fingerprint") != payload.get("preview_fingerprint")
        or prepared.get("record_revisions") != payload.get("record_revisions")
        or prepared.get("target_schema_revision")
        != payload.get("target_schema_revision")
        or prepared.get("target_schema_fingerprint")
        != payload.get("target_schema_fingerprint")
    ):
        return {
            "error": True,
            "error_code": "stale_preview",
            "message": "Entries or target schema changed after preview; prepare a fresh move",
        }

    moved_snapshots: List[Dict[str, Any]] = []

    async def apply() -> Dict[str, Any]:
        target = await Track.get(prepared["target_track_id"])
        if target is None:
            raise RuntimeError("Target Track schema changed during move")
        current_target_schema = await _track_schema_state(target)
        if (
            current_target_schema["revision"] != prepared["target_schema_revision"]
            or current_target_schema["fingerprint"]
            != prepared["target_schema_fingerprint"]
        ):
            raise RuntimeError("Target Track schema changed during move")
        target_app_id = await _app_id(target)
        moved = []
        for row in prepared["_rows"]:
            entry = await Entry.get(row["entry_id"])
            source = await Track.get(row["source_track_id"])
            if (
                entry is None
                or source is None
                or int(getattr(entry, "record_revision", 0) or 0)
                != int(prepared["record_revisions"].get(row["entry_id"], -1))
                or await _entry_track_id(entry) != row["source_track_id"]
            ):
                raise RuntimeError(f"Entry {row['entry_id']} changed during move")
            before = await entry.export(flat=True)
            ctx = await entry.get_context()
            old_contains = await ctx.find_edges_between(
                source.id, entry.id, edge_class=CONTAINS
            )
            for edge in old_contains:
                await edge.delete()
            await target.connect(entry, edge=CONTAINS, added_at=utc_now_iso())
            old_types = await ctx.find_edges_between(
                entry.id, str(entry.type_id), edge_class=IS_OF_TYPE
            )
            for edge in old_types:
                await edge.delete()
            new_type = await EntryType.get(row["_type_id"])
            if new_type is None:
                raise RuntimeError(
                    f"Destination EntryType disappeared for entry {entry.id}"
                )
            await entry.connect(new_type, edge=IS_OF_TYPE, assigned_at=utc_now_iso())

            # Keep all relation links and their typed metadata. Field-key mappings
            # update only the edge that represents the renamed relation field.
            field_map = payload.get("field_mapping", {}).get(
                row["source_entry_type"], {}
            )
            for edge_class, target_kind in (
                (REFERENCES, "Entry"),
                (ANCHORS, "Track"),
                (HAS_MEMBER_REF, "User"),
            ):
                for relation_target in await entry.nodes(
                    edge=[edge_class], direction="out", node=[target_kind]
                ):
                    for relation_edge in await ctx.find_edges_between(
                        entry.id, relation_target.id, edge_class=edge_class
                    ):
                        old_key = str(getattr(relation_edge, "field_key", "") or "")
                        if old_key in field_map and field_map[old_key] != old_key:
                            relation_edge.field_key = str(field_map[old_key])
                        if edge_class is REFERENCES:
                            related_track = await Track.get(
                                await _entry_track_id(relation_target)
                            )
                            related_app_id = (
                                await _app_id(related_track) if related_track else ""
                            )
                            relation_edge.cross_track = bool(
                                related_track and related_track.id != target.id
                            )
                            relation_edge.target_app_id = (
                                related_app_id
                                if related_app_id != target_app_id
                                else None
                            )
                        await relation_edge.save()

            # Inbound references retain their field keys, but their cross-track
            # and cross-App metadata describe the moved Entry as the target.
            for source_entry in await entry.nodes(
                edge=[REFERENCES], direction="in", node=["Entry"]
            ):
                source_track = await Track.get(await _entry_track_id(source_entry))
                source_app_id = await _app_id(source_track) if source_track else ""
                for relation_edge in await ctx.find_edges_between(
                    source_entry.id, entry.id, edge_class=REFERENCES
                ):
                    relation_edge.cross_track = bool(
                        source_track and source_track.id != target.id
                    )
                    relation_edge.target_app_id = (
                        target_app_id if source_app_id != target_app_id else None
                    )
                    await relation_edge.save()
            entry.track_id = target.id
            entry.type_id = new_type.id
            entry.custom_fields = row["_custom_fields"]
            entry.tags = list(getattr(entry, "tags", None) or [])
            source_tag_ids = list(row.get("_source_tag_ids") or [])
            target_tag_ids = list(row.get("_tag_ids") or [])
            for source_tag_id in source_tag_ids:
                mapped_tag_id = str(
                    (payload.get("tag_mapping") or {}).get(source_tag_id)
                    or source_tag_id
                )
                if mapped_tag_id == source_tag_id:
                    continue
                old_tag_edges = await ctx.find_edges_between(
                    entry.id, source_tag_id, edge_class=TAGGED_WITH
                )
                old_tag_edge = old_tag_edges[0] if old_tag_edges else None
                for edge in old_tag_edges:
                    await edge.delete()
                new_tag_edges = await ctx.find_edges_between(
                    entry.id, mapped_tag_id, edge_class=TAGGED_WITH
                )
                if not new_tag_edges:
                    mapped_tag = await Tag.get(mapped_tag_id)
                    if mapped_tag is None:
                        raise RuntimeError(
                            f"Destination Tag disappeared for entry {entry.id}"
                        )
                    await entry.connect(
                        mapped_tag,
                        edge=TAGGED_WITH,
                        tagged_at=(
                            getattr(old_tag_edge, "tagged_at", None)
                            if old_tag_edge
                            else utc_now_iso()
                        ),
                        tagged_by=(
                            getattr(old_tag_edge, "tagged_by", None)
                            if old_tag_edge
                            else user_id
                        ),
                    )
            entry.tags = target_tag_ids
            entry.schema_revision = int(prepared["target_schema_revision"])
            entry.record_revision = int(getattr(entry, "record_revision", 0) or 0) + 1
            entry.updated_at = utc_now_iso()
            await entry.save()
            moved_snapshots.append(
                {
                    "entry_id": entry.id,
                    "before": before,
                    "after": await entry.export(flat=True),
                    "source_track_id": source.id,
                }
            )
            moved.append(
                {
                    "entry_id": entry.id,
                    "source_track_id": source.id,
                    "target_track_id": target.id,
                    "status": "moved",
                }
            )
        if after_move is not None:
            await after_move()
        return {"moved": moved, "moved_count": len(moved), "target_track_id": target.id}

    try:
        if _transaction_internal:
            # The caller owns the surrounding graph transaction (used by
            # Track split, where the destination schema must be created and
            # validated before this prepared move can be applied).
            result = await apply()
        else:
            async with postgres_graph_transaction():
                result = await apply()
        from app.middleware.permissions_cache import reset_permissions_cache
        from app.services.permissions_process_cache import (
            clear_all as clear_permission_cache,
        )

        reset_permissions_cache()
        clear_permission_cache()
        for snapshot in moved_snapshots if emit_entry_audits else []:
            try:
                await emit_change_event(
                    actor_kind="human",
                    actor_id=user_id,
                    action="entry.update",
                    resource_type="Entry",
                    resource_id=snapshot["entry_id"],
                    before=snapshot["before"],
                    after=snapshot["after"],
                    scope=f"track:{prepared['target_track_id']}",
                    details={
                        "operation": "bulk_move",
                        "source_track_id": snapshot["source_track_id"],
                        "target_track_id": prepared["target_track_id"],
                    },
                )
            except (
                Exception
            ):  # noqa: BLE001 - committed data must not be reported as rolled back
                logger.exception(
                    "bulk move committed but audit event failed for entry %s",
                    snapshot["entry_id"],
                )
                result.setdefault("audit_event_warnings", []).append(
                    snapshot["entry_id"]
                )
        return result
    except OperationTransactionUnavailable:
        return {
            "error": True,
            "error_code": "transaction_unavailable",
            "message": "Bulk moves require a store that supports graph transactions",
        }
    except (
        Exception
    ) as exc:  # noqa: BLE001 — the transaction rolls back all graph writes
        return {
            "error": True,
            "error_code": "bulk_move_rolled_back",
            "message": str(exc) or "Bulk move was rolled back",
        }
