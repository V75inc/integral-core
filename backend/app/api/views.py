"""View CRUD API endpoints for saved track view configurations."""

import logging
from typing import Any, Dict, List, Optional

from fastapi import Request
from jvspatial.api import endpoint

from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    MissingAuthenticationError,
    ResourceNotFoundError,
)
from app.api.utils import export_node, resolve_principal_id
from app.api.validators_common import compute_fold, non_empty_after_strip
from app.models.edges import CATALOGS, CONTAINS
from app.models.nodes import Track, View
from app.schemas.policy import Resource, Subject
from app.services.app_graph import (
    catalog_view_under_track,
    get_or_create_views_registry_for_operational_model,
    get_track_attached_operational_model,
)
from app.services.change_event import emit_change_event
from app.services.operational_model_graph import is_space_track_template_profile
from app.services.operational_model_runtime import (
    backfill_view_entry_type_constraints_from_manifest,
    normalize_view_config,
    normalize_view_list_default_exports,
    pick_default_view_id_for_track,
    sync_attached_manifest,
)
from app.services.policy_engine import evaluate as policy_evaluate
from app.services.uniqueness import assert_unique
from app.utils.time import utc_now_iso
from app.views import operational_model_view_types as _view_type_registry

logger = logging.getLogger(__name__)


async def _maybe_sync_attached_manifest(cp) -> None:
    """Rebuild attached CP manifest unless it is a shared anchor-track template."""
    if cp and not is_space_track_template_profile(cp):
        await sync_attached_manifest(cp)


def _allowed_view_types() -> set:
    """Snapshot of registered view types (built-ins + plugins).

    Computed each call so plugins registered at startup are visible without
    reloading this module.
    """
    return set(_view_type_registry.allowed_keys())


def _slugify_key(value: str) -> str:
    """Normalize an entry-type label to its manifest-stable slug."""
    import re as _re

    s = str(value or "").strip().lower()
    s = _re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s


async def _entry_type_key_set_for_track(track: Track) -> set:
    """Return slug keys for EntryType nodes attached under this track."""
    from app.models.nodes import EntryType

    cp = await get_track_attached_operational_model(track)
    if not cp:
        return set()
    ets = await cp.nodes(edge=[CONTAINS], node=["EntryType"])
    return {
        _slugify_key(getattr(et, "name", "")) for et in ets if isinstance(et, EntryType)
    }


async def _normalize_entry_type_keys(
    track: Track,
    raw_keys: Optional[List[str]],
    raw_default: Optional[str],
) -> tuple[Optional[List[str]], Optional[str]]:
    """Validate + slug-normalize entry-type keys against the track's operational model.

    Returns ``(keys_or_None, default_or_None)``. ``None`` means "field not
    supplied" — callers should preserve the existing persisted value.
    """
    keys_out: Optional[List[str]] = None
    if raw_keys is not None:
        normalized = []
        seen = set()
        for k in raw_keys:
            slug = _slugify_key(k)
            if not slug or slug in seen:
                continue
            normalized.append(slug)
            seen.add(slug)
        if normalized:
            allowed = await _entry_type_key_set_for_track(track)
            unknown = [k for k in normalized if k not in allowed]
            if unknown:
                raise BadRequestError(
                    message=(
                        "entry_type_keys references unknown entry type(s): "
                        f"{', '.join(sorted(unknown))}"
                    ),
                    details={"unknown_keys": unknown, "allowed_keys": sorted(allowed)},
                )
        keys_out = normalized

    default_out: Optional[str] = None
    if raw_default is not None:
        default_out = _slugify_key(raw_default)
        if default_out:
            allowed = await _entry_type_key_set_for_track(track)
            if default_out not in allowed:
                raise BadRequestError(
                    message=(
                        f"default_entry_type_key '{default_out}' is not an entry "
                        "type on this track"
                    ),
                    details={"allowed_keys": sorted(allowed)},
                )
            # If keys constrained, default must be in the constrained set.
            if keys_out and default_out not in keys_out:
                raise BadRequestError(
                    message=(
                        "default_entry_type_key must be one of entry_type_keys when "
                        "entry_type_keys is set"
                    )
                )
    return keys_out, default_out


async def _list_track_views(track: Track) -> List[View]:
    cp = await get_track_attached_operational_model(track)
    if not cp:
        return []
    from app.services.operational_model_runtime import (
        ensure_track_views_materialized_from_tier,
    )

    await ensure_track_views_materialized_from_tier(track)
    vreg = await get_or_create_views_registry_for_operational_model(cp, track=track)
    views = await vreg.nodes(edge=[CATALOGS], node=["View"])
    # One-shot upgrade for Views created before entry_type_keys existed.
    # Idempotent — writes only when the View is empty AND the manifest spec
    # carries a value.
    await backfill_view_entry_type_constraints_from_manifest(track, views)
    # Anchored tracks sharing a template CP share one Views registry; scope
    # to this track BEFORE destructive dedupe so sibling tracks cannot
    # delete each other's View nodes.
    track_scoped = [
        v
        for v in views
        if isinstance(v, View) and str(getattr(v, "track_id", "") or "") == track.id
    ]
    # Heal legacy duplicate views (same _manifest_view_key + entry_type_keys
    # + track_id). Pre-idempotency, ensure_track_attached_operational_model +
    # library merge could each materialize the same view. Collapse them on
    # read: keep the canonical row (is_default first, then earliest
    # created_at) and delete the rest. Idempotent on already-clean tracks.
    return await _dedupe_duplicate_views(track_scoped)


def _dedupe_key(view: View) -> str:
    cfg = view.config or {}
    raw_key = str(cfg.get("_manifest_view_key") or "").strip().lower()
    etk = sorted(str(k).strip().lower() for k in (view.entry_type_keys or []))
    track_id = str(getattr(view, "track_id", "") or "").strip()
    return f"{view.type}::{raw_key}::{','.join(etk)}::{track_id}"


async def _dedupe_duplicate_views(views: List[View]) -> List[View]:
    """Collapse duplicate views sharing (type, manifest_key, entry_type_keys, track_id).

    Preserves the canonical row: default first, then earliest created_at.
    Returns the surviving list. Deletes the redundant View nodes — they
    were never reachable via the tab strip dedup logic anyway.
    """
    if not views:
        return views

    groups: Dict[str, List[View]] = {}
    for v in views:
        groups.setdefault(_dedupe_key(v), []).append(v)

    survivors: List[View] = []
    redundant: List[View] = []
    for bucket in groups.values():
        if len(bucket) == 1:
            survivors.append(bucket[0])
            continue
        bucket.sort(
            key=lambda v: (
                0 if getattr(v, "is_default", False) else 1,
                str(getattr(v, "created_at", "") or ""),
            )
        )
        survivors.append(bucket[0])
        redundant.extend(bucket[1:])

    for v in redundant:
        try:
            await v.delete()
        except Exception:
            # Best-effort heal; surface nothing if the delete races
            # another caller. Re-runs will retry.
            logger.warning("view-dedupe: failed to delete redundant view id=%s", v.id)

    return survivors


@endpoint("/tracks/{track_id}/views", methods=["GET"], auth=True, tags=["Views"])
async def list_track_views(
    request: Request,
    track_id: str,
) -> Dict[str, Any]:
    """List all saved views for a track."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="track.read",
        resource=Resource(kind="track", id=track_id, scope=f"track:{track_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")

    track = await Track.get(track_id)
    if not track:
        raise ResourceNotFoundError(message="Track not found")

    views = await _list_track_views(track)
    items = [await export_node(v) for v in views]
    # Use node-level is_default as the source of truth for the list response.
    # pick_default_view_id_for_track derives from the manifest and overrides any
    # explicit user selection made via PUT. Prefer the node flag; fall back to
    # manifest only when no node has is_default=True.
    winner_id = next((v.id for v in views if getattr(v, "is_default", False)), None)
    if not winner_id:
        winner_id = await pick_default_view_id_for_track(track, views)
    items = normalize_view_list_default_exports(items, winner_id)
    return {"views": items, "total": len(items), "track_id": track_id}


@endpoint("/tracks/{track_id}/views", methods=["POST"], auth=True, tags=["Views"])
async def create_view(
    request: Request,
    track_id: str,
    name: str,
    type: str = "feed",
    view_type: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
    is_default: bool = False,
    entry_type_keys: Optional[List[str]] = None,
    default_entry_type_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new saved view for a track (editor or owner only)."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="track.update",
        resource=Resource(kind="track", id=track_id, scope=f"track:{track_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")

    resolved_type = view_type or type
    valid_types = _allowed_view_types()
    if resolved_type not in valid_types:
        raise BadRequestError(
            message=f"type must be one of: {', '.join(sorted(valid_types))}",
        )
    normalized_config = normalize_view_config(resolved_type, config or {})

    track = await Track.get(track_id)
    if not track:
        raise ResourceNotFoundError(message="Track not found")

    safe_name = non_empty_after_strip(name, "name")
    if len(safe_name) > 120:
        raise BadRequestError(message="name must be 120 characters or fewer")
    name_fold = compute_fold(safe_name)
    await assert_unique(
        View,
        {"context.track_id": track_id, "context.name_fold": name_fold},
        entity="view",
        field_label="name",
        value=safe_name,
        scope_label="in this track",
    )

    now = utc_now_iso()

    if is_default:
        for v in await _list_track_views(track):
            if v.is_default:
                v.is_default = False
                await v.save()

    cp = await get_track_attached_operational_model(track)
    resolved_keys, resolved_default_key = await _normalize_entry_type_keys(
        track, entry_type_keys, default_entry_type_key
    )
    view = await View.create(
        name=safe_name,
        name_fold=name_fold,
        type=resolved_type,
        config=normalized_config,
        track_id=track_id,
        operational_model_id=cp.id if cp else "",
        entry_type_keys=resolved_keys or [],
        default_entry_type_key=resolved_default_key or "",
        is_default=is_default,
        created_by=user_id,
        created_at=now,
        updated_at=now,
    )

    await catalog_view_under_track(track, view)

    # Sync the attached operational model manifest
    if cp:
        await _maybe_sync_attached_manifest(cp)

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="view.create",
        resource_type="View",
        resource_id=view.id,
        before=None,
        after=await export_node(view),
        scope=f"track:{track_id}",
    )

    return {"view": await export_node(view), "message": "View created successfully"}


@endpoint("/views/{view_id}", methods=["GET"], auth=True, tags=["Views"])
async def get_view(request: Request, view_id: str) -> Dict[str, Any]:
    """Get a specific view by ID."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    view = await View.get(view_id)
    if not view:
        raise ResourceNotFoundError(message="View not found")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="view.read",
        resource=Resource(
            kind="view",
            id=view_id,
            scope=f"track:{view.track_id or ''}",
        ),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")
    return {"view": await export_node(view)}


@endpoint("/views/{view_id}", methods=["PUT"], auth=True, tags=["Views"])
async def update_view(
    request: Request,
    view_id: str,
    name: Optional[str] = None,
    type: Optional[str] = None,
    view_type: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
    is_default: Optional[bool] = None,
    hidden: Optional[bool] = None,
    entry_type_keys: Optional[List[str]] = None,
    default_entry_type_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Update a saved view (editor or owner of the track only)."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="view.update",
        resource=Resource(kind="view", id=view_id, scope=f"view:{view_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")

    view = await View.get(view_id)
    if not view:
        raise ResourceNotFoundError(message="View not found")

    prior_snapshot = await export_node(view)  # D-03 before-snapshot

    resolved_type = view_type or type
    if resolved_type is not None:
        valid_types = _allowed_view_types()
        if resolved_type not in valid_types:
            raise BadRequestError(
                message=f"type must be one of: {', '.join(sorted(valid_types))}",
            )

    if name is not None:
        safe_name = non_empty_after_strip(name, "name")
        if len(safe_name) > 120:
            raise BadRequestError(message="name must be 120 characters or fewer")
        new_fold = compute_fold(safe_name)
        if new_fold != (getattr(view, "name_fold", "") or ""):
            await assert_unique(
                View,
                {
                    "context.track_id": view.track_id,
                    "context.name_fold": new_fold,
                },
                entity="view",
                field_label="name",
                value=safe_name,
                scope_label="in this track",
                exclude_id=view.id,
            )
        view.name = safe_name
        view.name_fold = new_fold
    if resolved_type is not None:
        view.type = resolved_type
    if config is not None:
        view.config = normalize_view_config(resolved_type or view.type, config)

    if is_default is True:
        track = await Track.get(view.track_id)
        if track:
            all_views = await _list_track_views(track)
            for v in all_views:
                if v.id != view_id and v.is_default:
                    v.is_default = False
                    await v.save()
        view.is_default = True
        # Promoting a hidden view to default un-hides it — hidden+default is
        # an unreachable ghost state (no tab shows; default selector dead).
        if view.hidden:
            view.hidden = False
    elif is_default is False:
        view.is_default = False

    if hidden is True:
        # Hiding the current default: auto-demote, then promote the first
        # non-hidden sibling (prefer feed) so the track keeps a default.
        if view.is_default:
            view.is_default = False
            track = await Track.get(view.track_id)
            if track:
                siblings = [
                    v
                    for v in await _list_track_views(track)
                    if v.id != view_id and not getattr(v, "hidden", False)
                ]
                # Prefer feed view as fallback default; else first available.
                promoted = next((v for v in siblings if v.type == "feed"), None) or (
                    siblings[0] if siblings else None
                )
                if promoted is not None:
                    promoted.is_default = True
                    await promoted.save()
        view.hidden = True
    elif hidden is False:
        view.hidden = False

    if entry_type_keys is not None or default_entry_type_key is not None:
        track = await Track.get(view.track_id)
        if track is None:
            raise ResourceNotFoundError(message="View's track not found")
        new_keys, new_default = await _normalize_entry_type_keys(
            track, entry_type_keys, default_entry_type_key
        )
        if new_keys is not None:
            view.entry_type_keys = new_keys
        if new_default is not None:
            view.default_entry_type_key = new_default

    view.updated_at = utc_now_iso()
    await view.save()

    # Sync the attached operational model manifest — but SKIP when the only change is
    # is_default or hidden: sync_attached_manifest runs
    # synchronize_track_view_default_flags which re-derives defaults from the
    # manifest, overriding the user's explicit assignment. Skip sync for
    # flag-only updates so node values remain authoritative.
    _is_flag_only_update = (
        (is_default is not None or hidden is not None)
        and name is None
        and type is None
        and view_type is None
        and config is None
        and entry_type_keys is None
        and default_entry_type_key is None
    )
    if view.track_id and not _is_flag_only_update:
        t = await Track.get(view.track_id)
        if t:
            cp = await get_track_attached_operational_model(t)
            if cp:
                await _maybe_sync_attached_manifest(cp)

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="view.update",
        resource_type="View",
        resource_id=view.id,
        before=prior_snapshot,
        after=await export_node(view),
        scope=f"track:{view.track_id or ''}",
    )

    return {"view": await export_node(view), "message": "View updated successfully"}


@endpoint("/views/{view_id}", methods=["DELETE"], auth=True, tags=["Views"])
async def delete_view(request: Request, view_id: str) -> Dict[str, Any]:
    """Delete a saved view (editor or owner of the track only)."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="view.delete",
        resource=Resource(kind="view", id=view_id, scope=f"view:{view_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")

    view = await View.get(view_id)
    if not view:
        raise ResourceNotFoundError(message="View not found")

    prior_snapshot = await export_node(view)  # D-03 before-snapshot
    track_id_for_scope = view.track_id or ""

    # Sync manifest before deleting
    sync_cp = None
    if view.track_id:
        t = await Track.get(view.track_id)
        if t:
            sync_cp = await get_track_attached_operational_model(t)

    await view.delete()

    if sync_cp:
        await _maybe_sync_attached_manifest(sync_cp)

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="view.delete",
        resource_type="View",
        resource_id=view_id,
        before=prior_snapshot,
        after=None,
        scope=f"track:{track_id_for_scope}",
    )

    return {"message": "View deleted successfully", "deleted_view_id": view_id}
