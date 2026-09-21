"""EntryType service-layer helpers.

Wave 2 I-CRUD-01 — extracted from ``api/entry_types.py::create_entry_type``
so HTTP handlers share one graph-write path.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.api.errors import InsufficientPermissionsError, ResourceNotFoundError
from app.api.utils import export_node
from app.api.validators_common import compute_fold
from app.models.edges import CONTAINS
from app.models.nodes import EntryType, Track
from app.schemas.policy import Resource, Subject
from app.services.app_graph import (
    ensure_track_attached_operational_model,
    get_track_attached_operational_model,
)
from app.services.change_event import emit_change_event
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
    for spec in specs:
        name = str(spec.get("name") or spec.get("key") or "").strip()
        if not name:
            continue
        name_fold = compute_fold(name)
        existing = await EntryType.find(
            {"context.track_id": track.id, "context.name_fold": name_fold}
        )
        if not existing:
            # Legacy rows may lack name_fold — fall back to exact name match.
            existing = await EntryType.find(
                {"context.name": name, "context.track_id": track.id}
            )
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
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="track.update",
        resource=Resource(kind="track", id=track_id, scope=f"track:{track_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")

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
