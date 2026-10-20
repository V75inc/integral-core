"""Tag service-layer helpers.

Wave 3 — extracted from ``api/tags.py::create_tag`` so HTTP handlers and
agentive staging executors share one graph-write path (I-CRUD-01).
"""

from __future__ import annotations

from typing import List, Optional

from jvspatial.api.exceptions import InsufficientPermissionsError, ResourceNotFoundError

from app.exceptions import BadRequestError
from app.models.edges import CONTAINS
from app.models.nodes import App, Tag, Track
from app.schemas.policy import Resource, Subject
from app.services.app_graph import (
    ensure_app_attached_operational_model,
    ensure_track_attached_operational_model,
    get_app_attached_operational_model,
    get_track_attached_operational_model,
)
from app.services.change_event import emit_change_event
from app.services.operational_model_runtime import sync_attached_manifest
from app.services.policy_engine import evaluate as policy_evaluate
from app.services.uniqueness import assert_unique
from app.utils.time import utc_now_iso


async def assert_unique_tag_name(
    *, name: str, track_id: str = "", app_id: str = "", exclude_id: Optional[str] = None
) -> None:
    """Check normalized names, including tags seeded before fold keys existed."""
    from app.api.errors import ResourceConflictError
    from app.api.validators_common import compute_fold

    scope = {"context.track_id": track_id} if track_id else {"context.app_id": app_id}
    scope_label = "in this track" if track_id else "in this app"
    folded = compute_fold(name)
    await assert_unique(
        Tag,
        {**scope, "context.name_fold": folded},
        entity="tag",
        field_label="name",
        value=name,
        scope_label=scope_label,
        exclude_id=exclude_id,
    )
    legacy = await Tag.find({**scope, "context.name_fold": {"$in": ["", None]}})
    if any(tag.id != exclude_id and compute_fold(tag.name) == folded for tag in legacy):
        error = ResourceConflictError(
            message=f"Tag with name '{name}' already exists {scope_label}"
        )
        error.error_code = "resource.duplicate_tag"
        raise error


async def create_tag_for_scope(
    user_id: str,
    *,
    name: str,
    name_fold: str,
    color: str,
    track_id: str = "",
    app_id: str = "",
    group_key: Optional[str] = None,
    aliases: Optional[List[str]] = None,
    parent_tag_id: Optional[str] = None,
    applies_to_entry_types: Optional[List[str]] = None,
    required_workspace_id: Optional[str] = None,
) -> Tag:
    """Create a tag under a track or app-attached operational model.

    Mirrors the post-validation body of ``api/tags.py::create_tag``.
    Caller must enforce ``track_id`` xor ``app_id`` and run parent-cycle
    checks before invoking.
    """
    from app.api.utils import export_node

    tid = str(track_id or "").strip()
    sid = str(app_id or "").strip()
    if bool(tid) == bool(sid):
        raise BadRequestError(message="Provide exactly one of track_id or app_id")

    now = utc_now_iso()

    if tid:
        _decision = await policy_evaluate(
            subject=Subject(kind="human", id=user_id),
            action="track.update",
            resource=Resource(kind="track", id=tid, scope=f"track:{tid}"),
        )
        if not _decision.allowed:
            raise InsufficientPermissionsError(message="Access denied")
        await assert_unique_tag_name(name=name, track_id=tid)
        # Resolve the attachment point BEFORE creating the Tag. Creating first
        # and connecting conditionally leaves an orphan Node whenever the
        # lookup misses, which I-GRAPH-01 admits no exception for.
        track = await Track.get(tid)
        if not track:
            raise ResourceNotFoundError(message="Track not found")
        if required_workspace_id and track.workspace_id != required_workspace_id:
            raise BadRequestError(
                message="Track workspace does not match X-Integral-Scope"
            )
        cp = await get_track_attached_operational_model(track)
        if not cp:
            cp = await ensure_track_attached_operational_model(track)
        tag = await Tag.create(
            name=name,
            name_fold=name_fold,
            color=color,
            track_id=tid,
            app_id="",
            group_key=group_key,
            aliases=aliases or [],
            parent_tag_id=parent_tag_id,
            applies_to_entry_types=applies_to_entry_types or [],
            is_template=False,
            created_at=now,
        )
        await cp.connect(tag, edge=CONTAINS, added_at=now)
        await sync_attached_manifest(cp)
        await emit_change_event(
            actor_kind="human",
            actor_id=user_id,
            action="tag.create",
            resource_type="Tag",
            resource_id=tag.id,
            before=None,
            after=await export_node(tag),
            scope=f"track:{tid}",
        )
        return tag

    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="app.update",
        resource=Resource(kind="app", id=sid, scope=f"app:{sid}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")
    await assert_unique_tag_name(name=name, app_id=sid)
    sp = await App.get(sid)
    if not sp:
        raise ResourceNotFoundError(message="App not found")
    if required_workspace_id and sp.workspace_id != required_workspace_id:
        raise BadRequestError(message="App workspace does not match X-Integral-Scope")
    cp = await get_app_attached_operational_model(sp)
    if not cp:
        cp = await ensure_app_attached_operational_model(sp)
    tag = await Tag.create(
        name=name,
        name_fold=name_fold,
        color=color,
        track_id="",
        app_id=sid,
        group_key=group_key,
        aliases=aliases or [],
        parent_tag_id=parent_tag_id,
        applies_to_entry_types=applies_to_entry_types or [],
        is_template=False,
        created_at=now,
    )
    await cp.connect(tag, edge=CONTAINS, added_at=now)
    await sync_attached_manifest(cp)
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="tag.create",
        resource_type="Tag",
        resource_id=tag.id,
        before=None,
        after=await export_node(tag),
        scope=f"app:{sid}",
    )
    return tag
