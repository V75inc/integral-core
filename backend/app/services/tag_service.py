"""Tag service-layer helpers.

Wave 3 — extracted from ``api/tags.py::create_tag`` so HTTP handlers and
agentive staging executors share one graph-write path (I-CRUD-01).
"""

from __future__ import annotations

from typing import List, Optional

from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    ResourceNotFoundError,
)
from app.api.utils import export_node
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
) -> Tag:
    """Create a tag under a track or app-attached operational model.

    Mirrors the post-validation body of ``api/tags.py::create_tag``.
    Caller must enforce ``track_id`` xor ``app_id`` and run parent-cycle
    checks before invoking.
    """
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
        await assert_unique(
            Tag,
            {"context.track_id": tid, "context.name_fold": name_fold},
            entity="tag",
            field_label="name",
            value=name,
            scope_label="in this track",
        )
        # Resolve the attachment point BEFORE creating the Tag. Creating first
        # and connecting conditionally leaves an orphan Node whenever the
        # lookup misses, which I-GRAPH-01 admits no exception for.
        track = await Track.get(tid)
        if not track:
            raise ResourceNotFoundError(message="Track not found")
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
    await assert_unique(
        Tag,
        {"context.app_id": sid, "context.name_fold": name_fold},
        entity="tag",
        field_label="name",
        value=name,
        scope_label="in this app",
    )
    sp = await App.get(sid)
    if not sp:
        raise ResourceNotFoundError(message="App not found")
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
