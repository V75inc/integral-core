"""Transactional app/track ownership transfer (ARCHITECTURE §4.1, §9.7)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Dict

from app.api.errors import BadRequestError, ResourceNotFoundError
from app.models.edges import COLLABORATES_ON, OWNS
from app.services.permissions import get_user_node
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.models.nodes import App, Track


async def transfer_app_ownership(
    *,
    app_id: str,
    acting_user_id: str,
    new_owner_user_id: str,
) -> "App":
    """Promote an existing app collaborator to owner; demote prior owner to editor.

    Args:
        app_id: Target app_node.
        acting_user_id: Current owner (principal).
        new_owner_user_id: User node id of an existing collaborator.

    Returns:
        Updated App node.

    Raises:
        ResourceNotFoundError: Missing app or user.
        BadRequestError: Invalid transfer target or self-transfer.
    """
    from app.models.nodes import App

    if new_owner_user_id == acting_user_id:
        raise BadRequestError(message="Cannot transfer ownership to yourself")

    sp = await App.get(app_id)
    if not sp:
        raise ResourceNotFoundError(message="App not found")

    old_owner = await get_user_node(acting_user_id)
    new_owner = await get_user_node(new_owner_user_id)
    if not old_owner or not new_owner:
        raise ResourceNotFoundError(message="User not found")

    old_ctx = await old_owner.get_context()
    owns_edges = await old_ctx.find_edges_between(old_owner.id, app_id, edge_class=OWNS)
    if not owns_edges:
        raise BadRequestError(message="Only the App owner can transfer ownership")

    new_ctx = await new_owner.get_context()
    collab_edges = await new_ctx.find_edges_between(
        new_owner.id, app_id, edge_class=COLLABORATES_ON
    )
    if not collab_edges:
        raise BadRequestError(
            message="New owner must be an existing app collaborator",
        )

    now = utc_now_iso()

    for edge in collab_edges:
        await edge.delete()

    for edge in owns_edges:
        await edge.delete()

    await old_owner.connect(
        sp,
        edge=COLLABORATES_ON,
        role="editor",
        invited_at=now,
        invited_by=acting_user_id,
    )
    await new_owner.connect(sp, edge=OWNS, role="owner", granted_at=now)

    sp.owner_user_id = new_owner.id
    await sp.save()
    return sp


async def transfer_track_ownership(
    *,
    track_id: str,
    acting_user_id: str,
    new_owner_user_id: str,
) -> "Track":
    """Promote an existing track collaborator to owner; demote prior owner to editor."""
    from app.models.nodes import Track

    if new_owner_user_id == acting_user_id:
        raise BadRequestError(message="Cannot transfer ownership to yourself")

    track = await Track.get(track_id)
    if not track:
        raise ResourceNotFoundError(message="Track not found")

    old_owner = await get_user_node(acting_user_id)
    new_owner = await get_user_node(new_owner_user_id)
    if not old_owner or not new_owner:
        raise ResourceNotFoundError(message="User not found")

    old_ctx = await old_owner.get_context()
    owns_edges = await old_ctx.find_edges_between(
        old_owner.id, track_id, edge_class=OWNS
    )
    if not owns_edges:
        raise BadRequestError(message="Only the track owner can transfer ownership")

    new_ctx = await new_owner.get_context()
    collab_edges = await new_ctx.find_edges_between(
        new_owner.id, track_id, edge_class=COLLABORATES_ON
    )
    if not collab_edges:
        raise BadRequestError(
            message="New owner must be an existing track collaborator",
        )

    now = utc_now_iso()

    for edge in collab_edges:
        await edge.delete()

    for edge in owns_edges:
        await edge.delete()

    await old_owner.connect(
        track,
        edge=COLLABORATES_ON,
        role="editor",
        invited_at=now,
        invited_by=acting_user_id,
    )
    await new_owner.connect(track, edge=OWNS, role="owner", granted_at=now)

    track.owner_id = new_owner.id
    await track.save()
    return track


async def reassign_departing_member_ownership(
    user,
    workspace_id: str,
) -> Dict[str, int]:
    """Move this member's in-workspace ``OWNS`` edges to the workspace owner.

    Called when membership ends. The departing user keeps ``OWNS`` on
    resources in other workspaces. They are not left as an editor here —
    collaborator edges are dropped separately. The workspace owner's
    implicit role stays ``commenter`` on resources they do not own; this
    only moves the edges the departing member actually held.
    """
    moved = {"apps": 0, "tracks": 0}
    if not user or not workspace_id:
        return moved

    from app.services.permissions_process_cache import invalidate_user_aliases
    from app.services.workspace_permissions import get_workspace_owner_user_id

    owner_id = await get_workspace_owner_user_id(workspace_id)
    if not owner_id or owner_id == user.id:
        return moved
    owner = await get_user_node(owner_id)
    if not owner:
        logger.warning(
            "Cannot reassign ownership in workspace %s; owner user %s is missing",
            workspace_id,
            owner_id,
        )
        return moved

    resources = []
    try:
        for app in await user.nodes(
            edge=[OWNS], node=["WorkspaceApp"], direction="out"
        ):
            if str(getattr(app, "workspace_id", "") or "") == workspace_id:
                resources.append(("app", app))
        for track in await user.nodes(edge=[OWNS], node=["Track"], direction="out"):
            if str(getattr(track, "workspace_id", "") or "") == workspace_id:
                resources.append(("track", track))
    except Exception:
        logger.exception(
            "Failed listing OWNS edges for user %s in workspace %s",
            getattr(user, "id", ""),
            workspace_id,
        )
        return moved

    now = utc_now_iso()
    user_ctx = await user.get_context()
    owner_ctx = await owner.get_context()
    for kind, resource in resources:
        try:
            owns_edges = await user_ctx.find_edges_between(
                user.id, resource.id, edge_class=OWNS
            )
        except Exception:
            logger.exception(
                "OWNS lookup failed for user %s → %s", user.id, resource.id
            )
            continue
        if not owns_edges:
            continue
        try:
            for edge in await owner_ctx.find_edges_between(
                owner.id, resource.id, edge_class=COLLABORATES_ON
            ):
                await edge.delete()
            already_owns = await owner_ctx.find_edges_between(
                owner.id, resource.id, edge_class=OWNS
            )
        except Exception:
            logger.exception(
                "Workspace owner grant lookup failed for %s → %s",
                owner.id,
                resource.id,
            )
            continue
        for edge in owns_edges:
            await edge.delete()
        if not already_owns:
            await owner.connect(resource, edge=OWNS, role="owner", granted_at=now)
        if kind == "app":
            resource.owner_user_id = owner.id
            moved["apps"] += 1
        else:
            resource.owner_id = owner.id
            moved["tracks"] += 1
        await resource.save()

    if moved["apps"] or moved["tracks"]:
        invalidate_user_aliases(user)
        invalidate_user_aliases(owner)
    return moved
