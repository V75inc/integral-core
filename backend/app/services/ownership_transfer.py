"""Transactional app/track ownership transfer (ARCHITECTURE §4.1, §9.7)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

from jvspatial.core import Walker
from jvspatial.core.decorators import on_visit

from app.api.errors import BadRequestError, ResourceNotFoundError
from app.exceptions import AuthorizationUnavailableError
from app.models.edges import OWNS
from app.models.nodes import App as AppNode
from app.models.nodes import Track as TrackNode
from app.models.nodes import User
from app.services.grant_history import grant_baseline, record_grant_change
from app.services.invitation_grants import _current_grant
from app.services.permissions import get_user_node
from app.services.resource_location import locate_resource
from app.utils.time import utc_now_iso

if TYPE_CHECKING:
    from app.models.nodes import App, Track


async def transfer_app_ownership(
    *, app_id: str, acting_user_id: str, new_owner_user_id: str
) -> "App":
    """Legacy immediate transfer is closed; issue and accept a consent proposal."""
    from app.api.errors import ResourceConflictError

    raise ResourceConflictError(message="Explicit recipient ownership consent required")


async def transfer_track_ownership(
    *, track_id: str, acting_user_id: str, new_owner_user_id: str
) -> "Track":
    """Legacy immediate transfer is closed; issue and accept a consent proposal."""
    from app.api.errors import ResourceConflictError

    raise ResourceConflictError(message="Explicit recipient ownership consent required")


class DepartingOwnershipWalker(Walker):
    """Collect canonical ownership before cleanup, never trusting scalar scope alone."""

    workspace_id: str
    resources: Dict[str, Any] = {}

    @on_visit(User)
    async def on_user(self, here: User) -> None:
        targets = await here.nodes(
            edge=[OWNS], node=["WorkspaceApp", "Track"], direction="out", limit=10001
        )
        if len(targets) > 10000:
            raise AuthorizationUnavailableError()
        await self.visit(targets)

    @on_visit(AppNode, TrackNode)
    async def on_resource(self, here: Any) -> None:
        kind = "app" if isinstance(here, AppNode) else "track"
        location = await locate_resource(kind, here.id)
        if location is None:
            raise AuthorizationUnavailableError()
        if location.workspace_id == self.workspace_id:
            self.resources[here.id] = (kind, here)


async def reassign_departing_member_ownership(
    user, workspace_id: str
) -> Dict[str, int]:
    """Transfer only departing ownership, failing if its new owner is missing.

    The caller joins this to membership/grant removal in a graph transaction.
    On development stores, create the replacement edge before deleting the old
    one so an interrupted transfer cannot leave an unowned resource.
    """
    from app.runtime.composition import require_capability

    require_capability("collaboration")
    moved = {"apps": 0, "tracks": 0}
    if not user or not workspace_id:
        return moved

    from app.services.permissions_process_cache import invalidate_user_aliases
    from app.services.workspace_permissions import get_workspace_owner_user_id

    walker = DepartingOwnershipWalker(workspace_id=workspace_id)
    await walker.spawn(user)
    if any(
        isinstance(item, dict) and item.get("hook_error")
        for item in await walker.get_report()
    ):
        raise AuthorizationUnavailableError()
    resources = list(walker.resources.values())
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
        candidates = await ctx.database.find(
            "edge", {"target": resource.id, "entity": OWNS.__name__}, limit=2
        )
        if len(candidates) != 1 or candidates[0].get("source") != user.id:
            raise AuthorizationUnavailableError()
        already_owns = await ctx.find_edges_between(
            owner.id, resource.id, edge_class=OWNS
        )
        before_member = await _current_grant(user.id, resource.id, OWNS)
        before_owner = await _current_grant(owner.id, resource.id, OWNS)
        for subject, before in ((user, before_member), (owner, before_owner)):
            await grant_baseline(
                source_id=subject.id,
                target_id=resource.id,
                grant_class=OWNS,
                grant=before,
            )
        if not already_owns:
            await owner.connect(resource, edge=OWNS, role="owner", granted_at=now)
        if kind == "app":
            resource.owner_user_id = owner.id
            moved["apps"] += 1
        else:
            resource.owner_id = owner.id
            moved["tracks"] += 1
        await resource.save()
        for edge in owns_edges:
            await edge.delete()
        for subject, before in ((user, before_member), (owner, before_owner)):
            after = await _current_grant(subject.id, resource.id, OWNS)
            await record_grant_change(
                source=subject,
                target=resource,
                grant_class=OWNS,
                before=before,
                after=after,
            )

    invalidate_user_aliases(user)
    invalidate_user_aliases(owner)
    return moved
