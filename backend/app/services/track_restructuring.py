"""Preflighted Track merge and split operations (W4.4)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from jvspatial.core import Edge
from jvspatial.core.context import get_default_context

from app.models.edges import (
    ANCHORS,
    CATALOGS,
    COLLABORATES_ON,
    CONTAINS,
    EXCLUDED_FROM,
    HAS_OPERATIONAL_MODEL,
    HAS_SHARE_LINK,
    INVITED_TO,
)
from app.models.nodes import (
    App,
    EntryType,
    OperationalModel,
    Track,
    View,
    Views,
)
from app.schemas.policy import Resource, Subject
from app.services.app_graph import (
    ensure_catalog_edge,
    get_app_attached_operational_model,
    get_or_create_views_registry_for_operational_model,
    get_track_attached_operational_model,
)
from app.services.app_operations.transaction_scope import (
    graph_transaction_available,
)
from app.services.bulk_move_entries import (
    _app_id,
    _fingerprint,
    _type_key,
    move_entries,
    prepare_bulk_move,
)
from app.services.change_event import emit_change_event
from app.services.migration_write_guard import assert_track_schema_writable
from app.services.operational_model_compile import _slug
from app.services.policy_engine import evaluate as policy_evaluate
from app.utils.time import utc_now_iso

_MAX_ENTRIES = 500


async def _allowed(user_id: str, action: str, resource_id: str) -> bool:
    result = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action=action,
        resource=Resource(kind="track", id=resource_id, scope=f"track:{resource_id}"),
    )
    return bool(result.allowed)


async def _track_views(
    track: Track, model: OperationalModel
) -> tuple[Optional[Views], List[View]]:
    registries = await model.nodes(edge=[Edge], node=["Views"])
    if len(registries) > 1:
        raise ValueError(f"Track {track.id} has multiple Views registries")
    if not registries:
        return None, []
    registry = registries[0]
    views = await registry.nodes(edge=[CATALOGS], node=["View"])
    if any(view.track_id != track.id for view in views):
        raise ValueError(
            f"Views registry for Track {track.id} contains another Track's Views"
        )
    return registry, list(views)


def _custom_field_keys(entry_type: EntryType) -> List[str]:
    schema = getattr(entry_type, "form_schema", None) or {}
    fields = schema.get("fields", []) if isinstance(schema, dict) else []
    return sorted(
        {
            str(field.get("key") or "")
            for field in fields
            if isinstance(field, dict)
            and field.get("key")
            and not str(field.get("key")).startswith("_")
        }
    )


def _remap_view_data(value: Any, mapping: Dict[str, str]) -> Any:
    """Rewrite exact schema and taxonomy references in View JSON config."""
    if isinstance(value, dict):
        return {
            mapping.get(str(key), str(key)): _remap_view_data(item, mapping)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_remap_view_data(item, mapping) for item in value]
    if isinstance(value, str):
        return mapping.get(value, value)
    return value


async def prepare_track_merge(
    *,
    user_id: str,
    source_track_id: str,
    target_track_id: str,
    entry_type_mapping: Dict[str, str],
    field_mapping: Dict[str, Dict[str, str]],
    tag_mapping: Dict[str, str],
    view_mapping: Optional[Dict[str, str]] = None,
    workspace_id: str = "",
) -> Dict[str, Any]:
    """Validate a complete Track merge and return a revision-bound preview."""
    source = await Track.get(source_track_id)
    target = await Track.get(target_track_id)
    if source is None or target is None:
        return {"error": "not_found", "detail": "Source or target Track not found"}
    if source.id == target.id:
        return {"error": "same_track", "detail": "Source and target must differ"}
    source_workspace = str(source.workspace_id or "")
    target_workspace = str(target.workspace_id or "")
    if (
        not source_workspace
        or source_workspace != target_workspace
        or (workspace_id and source_workspace != workspace_id)
    ):
        return {
            "error": "workspace_mismatch",
            "detail": "Tracks must share the active Workspace",
        }
    source_app_id, target_app_id = await _app_id(source), await _app_id(target)
    if source_app_id != target_app_id:
        return {
            "error": "container_mismatch",
            "detail": "Tracks must belong to the same App or both be standalone",
        }
    if source.kind == "agent_scratch" or target.kind == "agent_scratch":
        return {
            "error": "unsupported_track_kind",
            "detail": "Scratch Tracks cannot be merged",
        }
    if source.visibility != target.visibility:
        return {
            "error": "visibility_mismatch",
            "detail": "Tracks with different visibility cannot be merged safely",
        }
    if source.template_id or target.template_id:
        return {
            "error": "template_track",
            "detail": "Template-derived Tracks cannot be merged",
        }

    for action, track in (
        ("track.update", source),
        ("track.delete", source),
        ("track.update", target),
    ):
        if not await _allowed(user_id, action, track.id):
            return {
                "error": "forbidden",
                "detail": f"Missing {action} authority on Track {track.id}",
            }
    try:
        await assert_track_schema_writable(source)
        await assert_track_schema_writable(target)
    except Exception as exc:  # noqa: BLE001 - surface migration guard refusal
        return {"error": "schema_not_writable", "detail": str(exc)}

    if not all(
        isinstance(value, dict)
        for value in (entry_type_mapping, field_mapping, tag_mapping)
    ):
        return {
            "error": "invalid_mapping",
            "detail": "EntryType, field, and Tag mappings must be objects",
        }
    view_mapping = dict(view_mapping or {})

    # Track-level grants and sidecars cannot be moved without changing their
    # security semantics. Fail closed before enumerating or moving Entries.
    checks = (
        (COLLABORATES_ON, "in", "User", "collaborators"),
        (EXCLUDED_FROM, "in", "User", "exclusions"),
        (INVITED_TO, "in", "Invitation", "invitations"),
        (HAS_SHARE_LINK, "out", "ShareLink", "share links"),
        (ANCHORS, "in", "Entry", "anchored parent Entries"),
    )
    for edge, direction, node_type, label in checks:
        related = await source.nodes(
            edge=[edge], direction=direction, node=[node_type], limit=1
        )
        if related:
            return {
                "error": "unsupported_sidecar",
                "detail": f"Source Track has {label}",
            }

    source_model = await get_track_attached_operational_model(source)
    target_model = await get_track_attached_operational_model(target)
    if source_model is None or target_model is None:
        return {
            "error": "operational_model_missing",
            "detail": "Both Tracks need attached Operational Models",
        }
    if source_model.id == target_model.id:
        return {
            "error": "shared_operational_model",
            "detail": "Tracks share an Operational Model",
        }
    referrers = await source_model.nodes(
        edge=[HAS_OPERATIONAL_MODEL], direction="in", node=["Track"], limit=2
    )
    if len(referrers) != 1 or referrers[0].id != source.id:
        return {
            "error": "shared_operational_model",
            "detail": "Source Operational Model is shared",
        }
    templates = await source_model.nodes(
        edge=[Edge], node=["OperationalModel"], limit=1
    )
    if templates:
        return {
            "error": "unsupported_model_sidecar",
            "detail": "Source Operational Model has templates",
        }

    source_types = await source_model.nodes(edge=[CONTAINS], node=["EntryType"])
    target_types = await target_model.nodes(edge=[CONTAINS], node=["EntryType"])
    source_by_key = {_type_key(item): item for item in source_types}
    target_by_key = {_type_key(item): item for item in target_types}
    if len(source_by_key) != len(source_types) or len(target_by_key) != len(
        target_types
    ):
        return {
            "error": "ambiguous_schema",
            "detail": "Duplicate EntryType keys prevent a safe merge",
        }
    if set(entry_type_mapping) != set(source_by_key):
        return {
            "error": "incomplete_entry_type_mapping",
            "detail": "Map every source EntryType exactly once",
        }
    if set(field_mapping) != set(source_by_key):
        return {
            "error": "incomplete_field_mapping",
            "detail": "Provide exactly one field mapping for every source EntryType",
        }
    normalized_type_map = {
        _slug(str(k)): _slug(str(v)) for k, v in entry_type_mapping.items()
    }
    normalized_field_map: Dict[str, Dict[str, str]] = {}
    for source_key, source_type in source_by_key.items():
        target_key = normalized_type_map.get(source_key)
        if target_key not in target_by_key:
            return {
                "error": "invalid_entry_type_mapping",
                "detail": f"No destination EntryType for {source_key}",
            }
        fields = field_mapping.get(source_key)
        if not isinstance(fields, dict) or set(fields) != set(
            _custom_field_keys(source_type)
        ):
            return {
                "error": "incomplete_field_mapping",
                "detail": f"Map every custom field on {source_key} explicitly",
            }
        normalized_fields = {str(key): str(value) for key, value in fields.items()}
        target_field_keys = set(_custom_field_keys(target_by_key[target_key]))
        if (
            len(set(normalized_fields.values())) != len(normalized_fields)
            or not set(normalized_fields.values()) <= target_field_keys
        ):
            return {
                "error": "invalid_field_mapping",
                "detail": f"Field mapping for {source_key} names an invalid or duplicate destination field",
            }
        normalized_field_map[source_key] = normalized_fields

    source_tags = [
        tag
        for tag in await source_model.nodes(edge=[CONTAINS], node=["Tag"])
        if tag.track_id == source.id
    ]
    target_tags = [
        tag
        for tag in await target_model.nodes(edge=[CONTAINS], node=["Tag"])
        if tag.track_id == target.id
    ]
    if source_app_id:
        app = await App.get(source_app_id)
        app_model = await get_app_attached_operational_model(app) if app else None
        if app_model:
            target_tags.extend(
                tag
                for tag in await app_model.nodes(edge=[CONTAINS], node=["Tag"])
                if tag.app_id == source_app_id and not tag.is_template
            )
    source_tag_ids = {str(tag.id) for tag in source_tags}
    target_tag_by_id = {str(tag.id): tag for tag in target_tags}
    if set(tag_mapping) != source_tag_ids:
        return {
            "error": "incomplete_tag_mapping",
            "detail": "Map every source Track Tag explicitly",
        }
    view_substitutions = dict(normalized_type_map)
    field_substitutions: Dict[str, str] = {}
    for fields in normalized_field_map.values():
        for source_key, target_key in fields.items():
            existing = field_substitutions.get(source_key)
            if existing is not None and existing != target_key:
                return {
                    "error": "incompatible_view_field_mapping",
                    "detail": f"Field {source_key!r} maps inconsistently across EntryTypes",
                }
            field_substitutions[source_key] = target_key
    view_substitutions.update(field_substitutions)
    for source_tag in source_tags:
        target_tag = target_tag_by_id.get(str(tag_mapping.get(source_tag.id) or ""))
        if target_tag is None:
            return {
                "error": "invalid_tag_mapping",
                "detail": f"Tag {source_tag.id} has no destination Tag in the target scope",
            }
        mapped_types = sorted(
            normalized_type_map.get(_slug(str(value)), _slug(str(value)))
            for value in source_tag.applies_to_entry_types or []
        )
        target_applies = sorted(
            _slug(str(value)) for value in target_tag.applies_to_entry_types or []
        )
        if (
            source_tag.group_key != target_tag.group_key
            or mapped_types != target_applies
            or (
                source_tag.parent_tag_id
                and tag_mapping.get(source_tag.parent_tag_id, source_tag.parent_tag_id)
                != target_tag.parent_tag_id
            )
        ):
            return {
                "error": "incompatible_tag_taxonomy",
                "detail": f"Tag {source_tag.id} has incompatible taxonomy",
            }
        view_substitutions[source_tag.id] = target_tag.id
        view_substitutions[source_tag.name] = target_tag.name

    try:
        source_view_registry, source_views = await _track_views(source, source_model)
        _target_view_registry, target_views = await _track_views(target, target_model)
    except ValueError as exc:
        return {"error": "unsupported_views_registry", "detail": str(exc)}
    target_names = {
        str(view.name_fold or view.name.casefold()) for view in target_views
    }
    target_has_default = any(view.is_default for view in target_views)
    source_default_claimed = False
    resolved_view_names: Dict[str, str] = {}
    for view in source_views:
        view_type_keys = {_slug(str(key)) for key in view.entry_type_keys or []}
        if view_type_keys - set(normalized_type_map):
            return {
                "error": "invalid_view_entry_type",
                "detail": f"View {view.name!r} refers to an unmapped EntryType",
            }
        default_type_key = _slug(str(view.default_entry_type_key or ""))
        if default_type_key and default_type_key not in normalized_type_map:
            return {
                "error": "invalid_view_entry_type",
                "detail": f"View {view.name!r} defaults to an unmapped EntryType",
            }
        name = str(view_mapping.get(view.id) or view.name)
        name_fold = name.strip().casefold()
        if not name.strip() or (
            name_fold in target_names and view.id not in view_mapping
        ):
            return {
                "error": "view_mapping_required",
                "detail": f"View {view.name!r} needs an explicit destination name",
            }
        if name_fold in target_names and view.id in view_mapping:
            return {
                "error": "view_name_conflict",
                "detail": f"Mapped View name {name!r} already exists",
            }
        target_names.add(name_fold)
        resolved_view_names[view.id] = name.strip()
        if view.is_default and not target_has_default:
            if source_default_claimed:
                return {
                    "error": "multiple_source_default_views",
                    "detail": "Source Track has more than one default View",
                }
            source_default_claimed = True
    if set(view_mapping) - {view.id for view in source_views}:
        return {
            "error": "invalid_view_mapping",
            "detail": "View mapping names a View outside the source Track",
        }

    rows, _ = await source.nodes_page(
        edge=[CONTAINS], node=["Entry"], limit=_MAX_ENTRIES + 1
    )
    if len(rows) > _MAX_ENTRIES:
        return {
            "error": "too_many_entries",
            "detail": f"Track merge is limited to {_MAX_ENTRIES} Entries",
        }
    bulk = await prepare_bulk_move(
        user_id=user_id,
        entry_ids=[entry.id for entry in rows],
        target_track_id=target.id,
        entry_type_mapping=normalized_type_map,
        field_mapping=field_mapping,
        tag_mapping=tag_mapping,
        allow_empty=True,
        workspace_id=workspace_id,
    )
    if bulk.get("error"):
        return bulk

    snapshot = {
        "source_track_id": source.id,
        "target_track_id": target.id,
        "workspace_id": source_workspace,
        "app_id": source_app_id,
        "source_visibility": source.visibility,
        "target_visibility": target.visibility,
        "source_schema_states": bulk["source_schema_states"],
        "target_schema_fingerprint": bulk["target_schema_fingerprint"],
        "source_model_id": source_model.id,
        "target_model_id": target_model.id,
        "source_model_version": int(source_model.version_number or 1),
        "source_model_updated_at": source_model.updated_at,
        "source_model_manifest": source_model.manifest or {},
        "entry_ids": sorted(entry.id for entry in rows),
        "entry_revisions": bulk["record_revisions"],
        "bulk_preview_fingerprint": bulk["preview_fingerprint"],
        "entry_type_mapping": normalized_type_map,
        "source_entry_types": [
            {"key": key, "name": item.name, "form_schema": item.form_schema}
            for key, item in sorted(source_by_key.items())
        ],
        "target_entry_types": [
            {"key": key, "name": item.name, "form_schema": item.form_schema}
            for key, item in sorted(target_by_key.items())
        ],
        "field_mapping": field_mapping,
        "tag_mapping": tag_mapping,
        "source_tag_taxonomy": [
            {
                "id": tag.id,
                "name": tag.name,
                "group_key": tag.group_key,
                "parent_tag_id": tag.parent_tag_id,
                "applies_to_entry_types": sorted(tag.applies_to_entry_types or []),
                "target_tag_id": tag_mapping.get(tag.id),
            }
            for tag in sorted(source_tags, key=lambda value: value.id)
        ],
        "target_tag_taxonomy": [
            {
                "id": tag.id,
                "name": tag.name,
                "group_key": tag.group_key,
                "parent_tag_id": tag.parent_tag_id,
                "applies_to_entry_types": sorted(tag.applies_to_entry_types or []),
            }
            for tag in sorted(target_tags, key=lambda value: value.id)
        ],
        "view_mapping": resolved_view_names,
        "views": [
            {
                "id": view.id,
                "name": view.name,
                "type": view.type,
                "config": view.config or {},
                "entry_type_keys": view.entry_type_keys or [],
                "default_entry_type_key": view.default_entry_type_key or "",
                "is_default": view.is_default,
                "hidden": view.hidden,
            }
            for view in source_views
        ],
    }
    return {
        "source_track_id": source.id,
        "target_track_id": target.id,
        "affected_count": len(rows),
        "entry_ids": snapshot["entry_ids"],
        "preview_fingerprint": _fingerprint(snapshot),
        "snapshot": snapshot,
        "bulk_preview_fingerprint": bulk["preview_fingerprint"],
        "record_revisions": bulk["record_revisions"],
        "target_schema_revision": bulk["target_schema_revision"],
        "target_schema_fingerprint": bulk["target_schema_fingerprint"],
        "entry_type_mapping": normalized_type_map,
        "field_mapping": field_mapping,
        "tag_mapping": tag_mapping,
        "view_mapping": resolved_view_names,
        "view_substitutions": view_substitutions,
        "_bulk": bulk,
        "_source_model": source_model,
        "_target_model": target_model,
        "_source_view_registry": source_view_registry,
        "_source_views": source_views,
        "_target_has_default_view": target_has_default,
    }


async def _delete_node_and_edges(node: Any) -> None:
    context = await node.get_context()
    edges = await context.find_edges_between(node.id)
    for edge in edges:
        await edge.delete()
    await node.delete(cascade=False)
    if await type(node).get(node.id) is not None:
        raise RuntimeError(f"Failed to delete {type(node).__name__} {node.id}")


async def merge_tracks(
    *, user_id: str, payload: Dict[str, Any], workspace_id: str = ""
) -> Dict[str, Any]:
    """Revalidate, move, transfer Views, and retire the source atomically."""
    if not graph_transaction_available():
        return {
            "error": True,
            "error_code": "transaction_unavailable",
            "message": "Track merges require graph transaction support",
        }
    prepared = await prepare_track_merge(
        user_id=user_id,
        source_track_id=str(payload.get("source_track_id") or ""),
        target_track_id=str(payload.get("target_track_id") or ""),
        entry_type_mapping=payload.get("entry_type_mapping") or {},
        field_mapping=payload.get("field_mapping") or {},
        tag_mapping=payload.get("tag_mapping") or {},
        view_mapping=payload.get("view_mapping") or {},
        workspace_id=workspace_id,
    )
    if prepared.get("error"):
        return {
            "error": True,
            "error_code": prepared["error"],
            "message": prepared["detail"],
        }
    if prepared["preview_fingerprint"] != payload.get("preview_fingerprint"):
        return {
            "error": True,
            "error_code": "stale_preview",
            "message": "Track, Entry, schema, taxonomy, or View state changed after preview",
        }

    source = await Track.get(prepared["source_track_id"])
    target = await Track.get(prepared["target_track_id"])
    if source is None or target is None:
        return {
            "error": True,
            "error_code": "not_found",
            "message": "Source or target Track disappeared",
        }

    async def retire_source() -> None:
        # Preflight objects carry cached contexts from before the transaction.
        # Rebind them so View/schema retirement participates in the same unit
        # of work as the Entry reparenting.
        transaction_context = get_default_context()
        for node in (
            source,
            target,
            prepared["_source_model"],
            prepared["_target_model"],
            prepared["_source_view_registry"],
            *prepared["_source_views"],
        ):
            if node is not None:
                await node.set_context(transaction_context)
        source_model = await get_track_attached_operational_model(source)
        target_model = await get_track_attached_operational_model(target)
        if source_model is None or target_model is None:
            raise RuntimeError("Operational Model disappeared during Track merge")
        registry = await get_or_create_views_registry_for_operational_model(
            target_model, track=target
        )
        for view in prepared["_source_views"]:
            target_name = prepared["view_mapping"][view.id]
            entry_type_keys = [
                prepared["entry_type_mapping"][_slug(str(key))]
                for key in (view.entry_type_keys or [])
            ]
            default_key = prepared["entry_type_mapping"].get(
                _slug(str(view.default_entry_type_key or "")), ""
            )
            clone = await View.create(
                name=target_name,
                name_fold=target_name.casefold(),
                type=view.type,
                config=_remap_view_data(
                    dict(view.config or {}), prepared["view_substitutions"]
                ),
                track_id=target.id,
                operational_model_id=target_model.id,
                entry_type_keys=entry_type_keys,
                default_entry_type_key=default_key,
                is_template=view.is_template,
                is_default=bool(
                    view.is_default and not prepared["_target_has_default_view"]
                ),
                hidden=view.hidden,
                created_by=view.created_by,
                created_at=utc_now_iso(),
                updated_at=utc_now_iso(),
            )
            await ensure_catalog_edge(registry, clone)
        from app.services.operational_model_runtime import sync_attached_manifest

        await sync_attached_manifest(target_model)
        if source_model is None:
            raise RuntimeError(
                "Source Operational Model disappeared during Track merge"
            )
        source_registry = prepared["_source_view_registry"]
        if source_registry:
            for view in prepared["_source_views"]:
                await _delete_node_and_edges(view)
            await _delete_node_and_edges(source_registry)
        source_types = await source_model.nodes(edge=[CONTAINS], node=["EntryType"])
        source_tags = await source_model.nodes(edge=[CONTAINS], node=["Tag"])
        for node in [*source_types, *source_tags]:
            await _delete_node_and_edges(node)
        await _delete_node_and_edges(source_model)
        await _delete_node_and_edges(source)

    move_payload = {
        "entry_ids": prepared["entry_ids"],
        "target_track_id": prepared["target_track_id"],
        "entry_type_mapping": prepared["entry_type_mapping"],
        "field_mapping": prepared["field_mapping"],
        "tag_mapping": prepared["tag_mapping"],
        "preview_fingerprint": prepared["bulk_preview_fingerprint"],
        "record_revisions": prepared["record_revisions"],
        "target_schema_revision": prepared["target_schema_revision"],
        "target_schema_fingerprint": prepared["target_schema_fingerprint"],
    }
    result = await move_entries(
        user_id=user_id,
        payload=move_payload,
        workspace_id=workspace_id,
        after_move=retire_source,
        allow_empty=True,
        emit_entry_audits=False,
    )
    if result.get("error"):
        return result
    try:
        await emit_change_event(
            actor_kind="human",
            actor_id=user_id,
            action="track.merge",
            resource_type="Track",
            resource_id=prepared["source_track_id"],
            before={"source_track_id": prepared["source_track_id"]},
            after={"target_track_id": prepared["target_track_id"]},
            scope=f"track:{prepared['target_track_id']}",
            details={
                "source_track_id": prepared["source_track_id"],
                "target_track_id": prepared["target_track_id"],
                "entry_ids": prepared["entry_ids"],
                "entry_type_mapping": prepared["entry_type_mapping"],
                "field_mapping": prepared["field_mapping"],
                "tag_mapping": prepared["tag_mapping"],
                "view_mapping": prepared["view_mapping"],
            },
        )
    except Exception as exc:  # noqa: BLE001 - graph commit already succeeded
        result.setdefault("audit_event_warnings", []).append(str(exc))
    return {
        "merged": True,
        "source_track_id": prepared["source_track_id"],
        "target_track_id": prepared["target_track_id"],
        "moved_entries": prepared["affected_count"],
    }
