"""EntryType service-layer helpers.

Wave 2 I-CRUD-01 — extracted from ``api/entry_types.py::create_entry_type``
so HTTP handlers share one graph-write path.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, List, Optional

from jvspatial.core.context import get_default_context

from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    ResourceNotFoundError,
)
from app.api.utils import export_node
from app.api.validators_common import compute_fold, non_empty_after_strip
from app.models.edges import CONTAINS
from app.models.nodes import EntryType, Track
from app.schemas.policy import Resource, Subject
from app.services.app_graph import (
    ensure_track_attached_operational_model,
    get_track_attached_operational_model,
)
from app.services.change_event import emit_change_event
from app.services.membership_write_scope import membership_write_scope
from app.services.operational_model_merge import (
    _form_schema_from_entry_type_spec,
    merge_entry_type_schema_from_spec,
)
from app.services.operational_model_runtime import (
    normalize_entry_type_form_schema,
    resolve_track_runtime_profile,
    sync_attached_manifest,
)
from app.services.policy_engine import evaluate as policy_evaluate
from app.services.uniqueness import assert_unique
from app.utils.time import utc_now_iso


async def assert_entry_type_deletable(entry_type: EntryType, profile: Any) -> None:
    """Keep the platform Post shape and the profile's final type available."""
    key = str((entry_type.form_schema or {}).get("_manifest_entry_type_key") or "")
    if entry_type.name.strip().casefold() == "post" or key == "post":
        raise BadRequestError(message="The default Post entry type cannot be deleted")
    if entry_type.track_id:
        track = await Track.get(entry_type.track_id)
        if track:
            _, tier, _ = await resolve_track_runtime_profile(track)
            default_key = str(
                (tier.get("defaults") or {}).get("default_entry_type") or ""
            )
            if default_key and default_key in {key, entry_type.name}:
                raise BadRequestError(
                    message="The default entry type cannot be deleted"
                )
    if await profile.count_nodes(node=[EntryType], edge=[CONTAINS]) <= 1:
        raise BadRequestError(message="The final entry type cannot be deleted")


def match_existing_entry_types(
    track_types: List[EntryType], name: str, name_fold: str
) -> List[EntryType]:
    """The EntryTypes of one Track that an entry-type spec already maps to.

    By ``name_fold`` first; when none match (legacy rows may lack ``name_fold``), by exact
    ``name``. Same two lookups ``materialize_entry_types_from_tier`` used to run as queries.
    """
    existing = [
        et
        for et in track_types
        if (getattr(et, "name_fold", None) or None) == name_fold
    ]
    if not existing:
        existing = [et for et in track_types if getattr(et, "name", None) == name]
    return existing


async def materialize_entry_types_from_tier(track: Track) -> List[EntryType]:
    """Create per-track EntryType nodes from the manifest tier (idempotent).

    Anchored tracks share a by-reference template OperationalModel whose
    ``entry_types[]`` live only in the manifest. The entry create path
    looks up types via ``EntryType.find({'context.track_id': ...})``, which
    returns empty for those tracks. This helper lazily clones the tier's
    entry-type specs into per-track EntryType nodes so type-bound APIs
    (entry create, type filtering, kanban grouping) keep working without
    breaking the template CP's by-reference contract. Returns the list of
    EntryType nodes now bound to this track.
    """
    cp = await get_track_attached_operational_model(track)
    if cp is None:
        return []
    _, tier, _ = await resolve_track_runtime_profile(track)
    specs = list(tier.get("entry_types") or [])
    if not specs:
        return []
    now = utc_now_iso()
    out: List[EntryType] = []
    # One read of the Track's EntryTypes instead of one or two lookups per spec. The matching
    # below is the two original queries done in memory: by name_fold first, then (legacy rows
    # without a name_fold) by exact name, in the store's order.
    track_types: List[EntryType] = list(
        await EntryType.find({"context.track_id": track.id})
    )
    for spec in specs:
        name = str(spec.get("name") or spec.get("key") or "").strip()
        if not name:
            continue
        name_fold = compute_fold(name)
        existing = match_existing_entry_types(track_types, name, name_fold)
        if existing:
            et0 = existing[0]
            if not (getattr(et0, "name_fold", "") or ""):
                et0.name_fold = name_fold
                et0.updated_at = now
                await et0.save()
            desired_schema = _form_schema_from_entry_type_spec(spec)
            spec_key = str(spec.get("key") or "")
            cur_norm = normalize_entry_type_form_schema(et0.form_schema or {})
            merged_schema, changed = merge_entry_type_schema_from_spec(
                cur_norm,
                desired_schema,
                spec_key=spec_key,
            )
            if changed:
                et0.form_schema = merged_schema
                et0.updated_at = now
                await et0.save()
            out.append(et0)
            continue
        et = await EntryType.create(
            name=name,
            name_fold=name_fold,
            icon=spec.get("icon") or "document",
            form_schema=_form_schema_from_entry_type_spec(spec),
            track_id=track.id,
            is_template=False,
            created_at=now,
            updated_at=now,
        )
        try:
            await cp.connect(et, edge=CONTAINS, added_at=now)
        except Exception:
            try:
                await et.delete()
            except Exception:
                pass
            raise
        track_types.append(et)
        out.append(et)
    return out


async def create_entry_type_for_track(
    user_id: str,
    *,
    track_id: str,
    name: str,
    name_fold: str,
    icon: str = "document",
    form_schema: Optional[Dict[str, Any]] = None,
) -> EntryType:
    """Create an EntryType under a track-attached operational model.

    Mirrors the post-validation body of ``api/entry_types.py::create_entry_type``.
    Caller must strip/validate ``name`` and compute ``name_fold`` before invoking.
    """
    caller_context = get_default_context()
    async with _entry_type_write_scope(user_id, track_id):
        await assert_unique(
            EntryType,
            {"context.track_id": track_id, "context.name_fold": name_fold},
            entity="entry_type",
            field_label="name",
            value=name,
            scope_label="in this track",
        )

        # Resolve attachment point BEFORE create — avoid I-GRAPH-01 orphans.
        track = await Track.get(track_id)
        if not track:
            raise ResourceNotFoundError(message="Track not found")
        cp = await get_track_attached_operational_model(track)
        if not cp:
            cp = await ensure_track_attached_operational_model(track)

        now = utc_now_iso()
        entry_type = await EntryType.create(
            name=name,
            name_fold=name_fold,
            icon=icon,
            form_schema=normalize_entry_type_form_schema(form_schema),
            track_id=track_id,
            is_template=False,
            created_at=now,
            updated_at=now,
        )
        try:
            await cp.connect(entry_type, edge=CONTAINS, added_at=now)
        except Exception:
            try:
                await entry_type.delete()
            except Exception:
                pass
            raise
        await sync_attached_manifest(cp)
    await entry_type.set_context(caller_context)

    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="entry_type.create",
        resource_type="EntryType",
        resource_id=entry_type.id,
        before=None,
        after=await export_node(entry_type),
        scope=f"track:{track_id}",
    )
    return entry_type


@asynccontextmanager
async def _entry_type_write_scope(user_id: str, track_id: str) -> AsyncIterator[None]:
    """Order track schema mutations with admitted Entry writes."""
    track = await Track.get(track_id)
    if track is None:
        raise ResourceNotFoundError(message="Track not found")
    async with membership_write_scope(track.workspace_id, principal_ids=(user_id,)):
        current = await Track.find_one({"id": track_id})
        if current is None or current.workspace_id != track.workspace_id:
            raise ResourceNotFoundError(message="Track no longer available")
        await _require_track_schema_authority(user_id, track_id)
        yield


async def _require_track_schema_authority(user_id: str, track_id: str) -> None:
    decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="track.update",
        resource=Resource(kind="track", id=track_id, scope=f"track:{track_id}"),
    )
    if not decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")


@asynccontextmanager
async def _admit_existing_entry_type(
    user_id: str, entry_type_id: str
) -> AsyncIterator[EntryType]:
    """Reload a track-local type inside its write fence."""
    entry_type = await EntryType.get(entry_type_id)
    if entry_type is None:
        raise ResourceNotFoundError(message="Entry type not found")
    if not entry_type.track_id:
        # Template and legacy types have no single track owner.
        yield entry_type
        return
    async with _entry_type_write_scope(user_id, entry_type.track_id):
        current = await EntryType.find_one({"id": entry_type_id})
        if current is None or current.track_id != entry_type.track_id:
            raise ResourceNotFoundError(message="Entry type no longer available")
        yield current


async def update_entry_type_for_actor(
    user_id: str,
    entry_type_id: str,
    *,
    name: Optional[str],
    icon: Optional[str],
    form_schema: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Update an EntryType and synchronize its attached model."""
    async with _admit_existing_entry_type(user_id, entry_type_id) as entry_type:
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

        # Sync the attached operational model manifest
        if entry_type.track_id:
            track = await Track.get(entry_type.track_id)
            if track:
                cp = await get_track_attached_operational_model(track)
                if cp:
                    await sync_attached_manifest(cp)
        after = await export_node(entry_type)
        track_id_for_scope = entry_type.track_id or ""

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="entry_type.update",
        resource_type="EntryType",
        resource_id=entry_type_id,
        before=prior_snapshot,
        after=after,
        scope=f"track:{track_id_for_scope}",
    )
    return after


async def delete_entry_type_for_actor(
    user_id: str,
    entry_type_id: str,
) -> None:
    """Delete an EntryType and synchronize its attached model."""
    async with _admit_existing_entry_type(user_id, entry_type_id) as entry_type:
        prior_snapshot = await export_node(entry_type)  # D-03 before-snapshot
        track_id_for_scope = entry_type.track_id or ""

        # Sync manifest before deleting the node (so manifest reflects removal)
        sync_cp = None
        if entry_type.track_id:
            track = await Track.get(entry_type.track_id)
            if track:
                sync_cp = await get_track_attached_operational_model(track)

        if sync_cp:
            await assert_entry_type_deletable(entry_type, sync_cp)

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
