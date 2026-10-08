"""Transactional app/track ownership transfer (ARCHITECTURE §4.1, §9.7)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict

from app.api.errors import BadRequestError, ResourceNotFoundError
from app.models.edges import COLLABORATES_ON, OWNS
from app.services.permissions import get_user_node
from app.utils.time import utc_now_iso

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
    user, workspace_id: str
) -> Dict[str, int]:
    """Transfer only departing ownership, failing if its new owner is missing.

    The caller joins this to membership/grant removal in a graph transaction.
    On development stores, create the replacement edge before deleting the old
    one so an interrupted transfer cannot leave an unowned resource.
    """
    moved = {"apps": 0, "tracks": 0}
    if not user or not workspace_id:
        return moved

    from app.services.permissions_process_cache import invalidate_user_aliases
    from app.services.workspace_permissions import get_workspace_owner_user_id

    resources = []
    for node_type, kind in (("WorkspaceApp", "app"), ("Track", "track")):
        for resource in await user.nodes(
            edge=[OWNS], node=[node_type], direction="out"
        ):
            if str(getattr(resource, "workspace_id", "") or "") == workspace_id:
                resources.append((kind, resource))
    if not resources:
        return moved

    owner_id = await get_workspace_owner_user_id(workspace_id)
    owner = await get_user_node(owner_id) if owner_id else None
    if owner is None:
        raise ResourceNotFoundError(message="Workspace owner is unavailable")
    if owner.id == user.id:
        raise BadRequestError(message="Cannot remove the workspace owner")

    now = utc_now_iso()
    ctx = await user.get_context()
    for kind, resource in resources:
        owns_edges = await ctx.find_edges_between(user.id, resource.id, edge_class=OWNS)
        if not owns_edges:
            continue
        already_owns = await ctx.find_edges_between(
            owner.id, resource.id, edge_class=OWNS
        )
        if not already_owns:
            await owner.connect(resource, edge=OWNS, role="owner", granted_at=now)
        for edge in await ctx.find_edges_between(
            owner.id, resource.id, edge_class=COLLABORATES_ON
        ):
            await edge.delete()
        if kind == "app":
            resource.owner_user_id = owner.id
            moved["apps"] += 1
        else:
            resource.owner_id = owner.id
            moved["tracks"] += 1
        await resource.save()
        for edge in owns_edges:
            await edge.delete()

    invalidate_user_aliases(user)
    invalidate_user_aliases(owner)
    return moved
