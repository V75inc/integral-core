"""EntryType CRUD API endpoints with track scoping and permission checks."""

import logging
from typing import Any, Dict, Optional

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
from app.models.edges import CONTAINS
from app.models.nodes import EntryType, Track
from app.schemas.policy import Resource, Subject
from app.services.app_graph import get_track_attached_content_profile
from app.services.change_event import emit_change_event
from app.services.content_profile_runtime import (
    normalize_entry_type_form_schema,
    slug_manifest_key,
    sync_attached_manifest,
)
from app.services.entry_type_service import create_entry_type_for_track
from app.services.policy_engine import evaluate as policy_evaluate
from app.services.uniqueness import assert_unique
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)


@endpoint("/entry-types", methods=["GET"], auth=True, tags=["Entry Types"])
async def list_entry_types(
    request: Request,
    track_id: Optional[str] = None,
) -> Dict[str, Any]:
    """List entry types, optionally scoped to a specific track."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    if track_id:
        _decision = await policy_evaluate(
            subject=Subject(kind="human", id=user_id),
            action="track.read",
            resource=Resource(kind="track", id=track_id, scope=f"track:{track_id}"),
        )
        if not _decision.allowed:
            raise InsufficientPermissionsError(message="Access denied")
        track = await Track.get(track_id)
        if track is not None:
            try:
                from app.services.entry_type_service import (
                    materialize_entry_types_from_tier,
                )

                # Track-local rematerialize only — never run update_app_from_library
                # (or owner-impersonating heal) on a track.read path.
                await materialize_entry_types_from_tier(track)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "list_entry_types: rematerialize failed for track %s",
                    track_id,
                )
        entry_types = await EntryType.find({"context.track_id": track_id})
        if not entry_types:
            # Fallback for Tracks whose attached ContentProfile is shared
            # by-reference rather than track-owned (the Anchor Pattern's
            # auto-provisioned template Tracks — materialize_anchor_track
            # attaches the SAME template ContentProfile to every Track
            # anchored from a given template_key, so its EntryType nodes
            # carry no single track_id to match). Mirrors
            # ``_list_track_views`` (app/api/views.py), which already
            # resolves Views the same way for the identical reason.
            track = await Track.get(track_id)
            if track:
                cp = await get_track_attached_content_profile(track)
                if cp:
                    entry_types = await cp.nodes(edge=[CONTAINS], node=["EntryType"])
    else:
        entry_types = await EntryType.find()

    items = [await export_node(et) for et in entry_types]
    for item, et in zip(items, entry_types):
        item["key"] = _entry_type_key(et)
    return {"entry_types": items, "total": len(items)}


def _entry_type_key(et: EntryType) -> str:
    """The manifest key an agent should pass as ``type_hint``/``entry_type``.

    EntryType nodes carry no top-level ``key`` field — export_node alone
    surfaces only ``name`` (the display label), leaving the manifest key
    (e.g. "pay_run") buried inside form_schema._manifest_entry_type_key
    where callers reading this list response never see it (found live:
    the resident asked the user to disambiguate an entry type it could
    have named itself). Same resolution precedence as
    entry_type_resolver.resolve_entry_type_id_by_key: the embedded
    manifest key first, a name-derived slug for entry types materialized
    before that field existed.
    """
    manifest_key = slug_manifest_key(
        str((et.form_schema or {}).get("_manifest_entry_type_key") or "")
    )
    return manifest_key or slug_manifest_key(str(et.name or ""))


@endpoint("/entry-types", methods=["POST"], auth=True, tags=["Entry Types"])
async def create_entry_type(
    request: Request,
    track_id: str,
    name: str,
    icon: str = "document",
    form_schema: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create a new entry type scoped to a track (editor or owner only)."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    safe_name = non_empty_after_strip(name, "name")
    if len(safe_name) > 120:
        raise BadRequestError(message="name must be 120 characters or fewer")
    name_fold = compute_fold(safe_name)

    entry_type = await create_entry_type_for_track(
        user_id,
        track_id=track_id,
        name=safe_name,
        name_fold=name_fold,
        icon=icon,
        form_schema=form_schema,
    )

    return {
        "entry_type": await export_node(entry_type),
        "message": "Entry type created successfully",
    }


@endpoint(
    "/entry-types/{entry_type_id}", methods=["GET"], auth=True, tags=["Entry Types"]
)
async def get_entry_type(request: Request, entry_type_id: str) -> Dict[str, Any]:
    """Get a specific entry type by ID."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    entry_type = await EntryType.get(entry_type_id)
    if not entry_type:
        raise ResourceNotFoundError(message="Entry type not found")

    if entry_type.track_id:
        _decision = await policy_evaluate(
            subject=Subject(kind="human", id=user_id),
            action="track.read",
            resource=Resource(
                kind="track",
                id=entry_type.track_id,
                scope=f"track:{entry_type.track_id}",
            ),
        )
        if not _decision.allowed:
            raise InsufficientPermissionsError(message="Access denied")

    return {"entry_type": await export_node(entry_type)}


@endpoint(
    "/entry-types/{entry_type_id}", methods=["PUT"], auth=True, tags=["Entry Types"]
)
async def update_entry_type(
    request: Request,
    entry_type_id: str,
    name: Optional[str] = None,
    icon: Optional[str] = None,
    form_schema: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Update an entry type (editor or owner of the owning track only)."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    entry_type = await EntryType.get(entry_type_id)
    if not entry_type:
        raise ResourceNotFoundError(message="Entry type not found")

    if entry_type.track_id:
        _decision = await policy_evaluate(
            subject=Subject(kind="human", id=user_id),
            action="track.update",
            resource=Resource(
                kind="track",
                id=entry_type.track_id,
                scope=f"track:{entry_type.track_id}",
            ),
        )
        if not _decision.allowed:
            raise InsufficientPermissionsError(message="Access denied")

    prior_snapshot = await export_node(entry_type)  # D-03 before-snapshot

    if name is not None:
        safe_name = non_empty_after_strip(name, "name")
        if len(safe_name) > 120:
            raise BadRequestError(message="name must be 120 characters or fewer")
        new_fold = compute_fold(safe_name)
        if new_fold != (getattr(entry_type, "name_fold", "") or ""):
            await assert_unique(
                EntryType,
                {
                    "context.track_id": entry_type.track_id,
                    "context.name_fold": new_fold,
                },
                entity="entry_type",
                field_label="name",
                value=safe_name,
                scope_label="in this track",
                exclude_id=entry_type.id,
            )
        entry_type.name = safe_name
        entry_type.name_fold = new_fold
    if icon is not None:
        entry_type.icon = icon
    if form_schema is not None:
        entry_type.form_schema = normalize_entry_type_form_schema(form_schema)

    entry_type.updated_at = utc_now_iso()
    await entry_type.save()

    # Sync the attached profile manifest
    if entry_type.track_id:
        track = await Track.get(entry_type.track_id)
        if track:
            cp = await get_track_attached_content_profile(track)
            if cp:
                await sync_attached_manifest(cp)

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="entry_type.update",
        resource_type="EntryType",
        resource_id=entry_type.id,
        before=prior_snapshot,
        after=await export_node(entry_type),
        scope=f"track:{entry_type.track_id or ''}",
    )

    return {
        "entry_type": await export_node(entry_type),
        "message": "Entry type updated successfully",
    }


@endpoint(
    "/entry-types/{entry_type_id}", methods=["DELETE"], auth=True, tags=["Entry Types"]
)
async def delete_entry_type(request: Request, entry_type_id: str) -> Dict[str, Any]:
    """Delete an entry type (editor or owner of the owning track only)."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    entry_type = await EntryType.get(entry_type_id)
    if not entry_type:
        raise ResourceNotFoundError(message="Entry type not found")

    if entry_type.track_id:
        _decision = await policy_evaluate(
            subject=Subject(kind="human", id=user_id),
            action="track.update",
            resource=Resource(
                kind="track",
                id=entry_type.track_id,
                scope=f"track:{entry_type.track_id}",
            ),
        )
        if not _decision.allowed:
            raise InsufficientPermissionsError(message="Access denied")

    prior_snapshot = await export_node(entry_type)  # D-03 before-snapshot
    track_id_for_scope = entry_type.track_id or ""

    # Sync manifest before deleting the node (so manifest reflects removal)
    sync_cp = None
    if entry_type.track_id:
        track = await Track.get(entry_type.track_id)
        if track:
            sync_cp = await get_track_attached_content_profile(track)

    await entry_type.delete()

    if sync_cp:
        await sync_attached_manifest(sync_cp)

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="entry_type.delete",
        resource_type="EntryType",
        resource_id=entry_type_id,
        before=prior_snapshot,
        after=None,
        scope=f"track:{track_id_for_scope}",
    )

    return {
        "message": "Entry type deleted successfully",
        "deleted_entry_type_id": entry_type_id,
    }
