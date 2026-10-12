"""Workspace-level permission helpers.

Single authority on Workspace-level access. Both Personal and Organization
workspaces resolve ownership and membership through the same
``User —IS_MEMBER_OF{role}→ Workspace`` edge — Personal has exactly one
such edge (``role: "owner"``) created alongside the workspace itself;
Organization has one edge per member with role in
``{owner, admin, member, guest}``.

  - Personal: exactly one member (the owner). No invitations, no cascade
    beyond the owner.
  - Organization: members via ``IS_MEMBER_OF`` edge; owner role grants
    admin + cascade; guest role grants member-pool inclusion but NOT
    visibility-cascade access.
"""

from __future__ import annotations

import logging
from typing import Literal, Optional, Tuple

from app.exceptions import AuthorizationUnavailableError
from app.middleware.permissions_cache import isolated_permissions_cache
from app.models.edges import IS_MEMBER_OF
from app.models.nodes import App, Track, User, Workspace
from app.services.authority_facts import relationship_flag, relationship_literal

logger = logging.getLogger(__name__)

WorkspaceRole = Literal["owner", "admin", "member", "guest", "none"]


def _member_row_role(row: dict) -> Optional[str]:
    """Require the stored literal; malformed facts never activate legacy grants."""
    return relationship_literal(row, "role", {"owner", "admin", "member", "guest"})


async def read_member_facts(user: User, workspace_id: str) -> Optional[dict]:
    """Read the complete single membership and validate its grant vocabulary."""
    try:
        ctx = await user.get_context()
        rows = await ctx.database.find(
            "edge",
            {
                "source": user.id,
                "target": workspace_id,
                "entity": IS_MEMBER_OF.__name__,
            },
            limit=2,
        )
        if not rows:
            return None
        if len(rows) != 1:
            raise AuthorizationUnavailableError()
        if (
            rows[0].get("source") != user.id
            or rows[0].get("target") != workspace_id
            or rows[0].get("entity") != IS_MEMBER_OF.__name__
        ):
            raise AuthorizationUnavailableError()
        _member_row_role(rows[0])
        return rows[0]
    except Exception as exc:
        raise AuthorizationUnavailableError() from exc


async def member_creation_flags(user: User, workspace_id: str) -> Tuple[bool, bool]:
    """Return explicit, uncoerced grants from the unique current membership."""
    from app.services.workspace_suspension import workspace_suspended

    if await workspace_suspended(user.id, workspace_id):
        return False, False
    row = await read_member_facts(user, workspace_id)
    if row is None:
        return False, False
    return (
        relationship_flag(row, "can_create_apps"),
        relationship_flag(row, "can_create_tracks"),
    )


async def _read_member_edge_role(user: User, workspace_id: str) -> Optional[str]:
    """Return the role stored on the IS_MEMBER_OF edge, or None."""
    try:
        row = await read_member_facts(user, workspace_id)
        return _member_row_role(row) if row is not None else None
    except Exception as exc:
        logger.exception(
            "_read_member_edge_role failed for %s → %s", user.id, workspace_id
        )
        raise AuthorizationUnavailableError() from exc


async def _canonical_owner_id(workspace: Workspace) -> Optional[str]:
    """Resolve one owner from complete current membership facts."""
    ctx = await workspace.get_context()
    rows = await ctx.database.find(
        "edge",
        {
            "target": workspace.id,
            "entity": IS_MEMBER_OF.__name__,
            "$or": [{"context.role": "owner"}, {"role": "owner"}],
        },
        limit=2,
    )
    owners = []
    for row in rows:
        if (
            row.get("target") != workspace.id
            or row.get("entity") != IS_MEMBER_OF.__name__
            or not isinstance(row.get("source"), str)
        ):
            raise AuthorizationUnavailableError()
        if _member_row_role(row) == "owner":
            owners.append(row["source"])
    if len(owners) != 1 or not owners[0]:
        return None
    from app.services.permissions import get_user_node

    owner = await get_user_node(owners[0])
    if owner is None or owner.id != owners[0]:
        return None
    membership = await read_member_facts(owner, workspace.id)
    return (
        owner.id
        if membership is not None and _member_row_role(membership) == "owner"
        else None
    )


async def get_workspace_owner_user_id(workspace_id: str) -> Optional[str]:
    """Return the unique live canonical owner, or None for ambiguous/missing facts."""
    try:
        with isolated_permissions_cache():
            workspace = await Workspace.find_one({"id": workspace_id})
            if workspace is None:
                return None
            return await _canonical_owner_id(workspace)
    except Exception as exc:
        logger.exception("get_workspace_owner_user_id failed for %s", workspace_id)
        raise AuthorizationUnavailableError() from exc


async def _current_workspace_role(user: User, workspace: Workspace) -> WorkspaceRole:
    """Read authority for an already freshly checked principal and workspace."""
    try:
        from app.services.workspace_suspension import workspace_suspended

        if await workspace_suspended(user.id, workspace.id):
            return "none"
        ctx = await workspace.get_context()
        # One store read gets both the caller's complete membership and the
        # canonical owner candidates. Separate reads measured 33 feed trips
        # against the unchanged budget of 32 (authority-facts regression).
        rows = await ctx.database.find(
            "edge",
            {
                "target": workspace.id,
                "entity": IS_MEMBER_OF.__name__,
                "$or": [
                    {"source": user.id},
                    {"context.role": "owner"},
                    {"role": "owner"},
                ],
            },
            limit=3,
        )
        # One caller membership plus one owner is the largest valid set.
        # A third candidate proves ambiguity, regardless of row order.
        if len(rows) > 2:
            return "none"
        for row in rows:
            if (
                row.get("target") != workspace.id
                or row.get("entity") != IS_MEMBER_OF.__name__
                or not isinstance(row.get("source"), str)
            ):
                raise AuthorizationUnavailableError()
            _member_row_role(row)
        memberships = [row for row in rows if row.get("source") == user.id]
        if len(memberships) > 1:
            raise AuthorizationUnavailableError()
        if len(memberships) != 1:
            return "none"
        role = _member_row_role(memberships[0])
        if role == "owner":
            owners = [row for row in rows if _member_row_role(row) == "owner"]
            if len(owners) != 1 or owners[0].get("source") != user.id:
                return "none"
        return role or "none"  # type: ignore[return-value]
    except Exception as exc:
        raise AuthorizationUnavailableError() from exc


async def can_access_workspace(user_id: str, workspace_id: str) -> WorkspaceRole:
    """Return current owner/admin/member/guest authority, or none."""
    from app.services.permissions import get_user_node

    try:
        with isolated_permissions_cache():
            user = await get_user_node(user_id)
            if user is None:
                return "none"
            workspace = await Workspace.find_one({"id": workspace_id})
            if workspace is None:
                return "none"
            return await _current_workspace_role(user, workspace)
    except Exception as exc:
        raise AuthorizationUnavailableError() from exc


async def user_in_workspace_member_pool(user_id: str, workspace_id: str) -> bool:
    """True if the user has any role (including guest) in the workspace.

    For Personal workspaces this is equivalent to ownership; for
    Organization workspaces it tests presence of the IS_MEMBER_OF edge.
    """
    role = await can_access_workspace(user_id, workspace_id)
    return role != "none"


async def user_in_workspace_cascade_view(user_id: str, workspace_id: str) -> bool:
    """True if the user has visibility-cascade access (excludes guest).

    Equivalent to the prior ``_user_in_organization_cascade_view`` but
    applies uniformly to Personal (owner) and Organization (non-guest).
    """
    role = await can_access_workspace(user_id, workspace_id)
    return role in ("owner", "admin", "member")


async def is_workspace_owner(user_id: str, workspace_id: str) -> bool:
    """True iff ``user_id`` is the owner of ``workspace_id``."""
    return (await can_access_workspace(user_id, workspace_id)) == "owner"


async def is_workspace_admin_or_owner(user_id: str, workspace_id: str) -> bool:
    """True when the user holds workspace-level owner or admin role."""
    return (await can_access_workspace(user_id, workspace_id)) in ("owner", "admin")


async def workspace_staff_implicit_resource_role(
    user_id: str, workspace_id: str
) -> Optional[str]:
    """Implicit app/track role for org workspace owner/admin.

    Workspace membership does not cascade to children by default, but
    owner/admin staff need inventory visibility on non-private Apps and
    Tracks without a per-resource ``COLLABORATES_ON`` edge. Private
    resources stay with their owner and the people that owner adds;
    ``resolve_role`` drops this implicit role when effective visibility
    is ``private``.
    """
    ws = await Workspace.find_one({"id": workspace_id})
    if not ws or getattr(ws, "kind", "") != "organization":
        return None
    role = await can_access_workspace(user_id, workspace_id)
    if role in ("owner", "admin"):
        # Read + participate, nothing more. Org staff must still hold a direct
        # owner/admin grant on a resource to edit entries, mint shares or
        # manage collaborators — `commenter` is one rank below `editor`, so
        # every one of those gates (can_edit_*, can_admin_*) stays closed.
        #
        # This was `viewer` until the "users with full permissions cannot add
        # comments" report was filed twice (QA July 1, again August 5). The
        # comment gate is `commenter`-or-better, so a workspace admin — someone
        # who administers the member pool, invitations and settings — could
        # read an entry and had no way to reply to it, and no explanation why.
        # Being unable to participate in a discussion is not a meaningful
        # safeguard; being unable to edit, share or re-permission is, and that
        # line is unchanged.
        #
        # Matrix pinned in tests/test_comment_permission_matrix.py; the
        # authority half is pinned from the other side by
        # test_org_admin_cannot_mint_share_without_direct_grant and
        # test_workspace_admin_implicit_role_is_commenter_on_inventory_resources.
        return "commenter"
    return None


async def collect_org_workspace_staff_inventory(
    workspace: Workspace,
) -> Tuple[list[App], list[Track]]:
    """Discover bounded primary candidates; final canonical checks admit each.

    Read discovery must not reuse strict destructive inventory. An uncatalogued
    scalar-only resource is a candidate for denial, not permission to hide all
    otherwise valid resources. Current canonical scope still gates the result.
    """
    from jvspatial.core.context import get_default_context

    from app.services.authority_facts import required_node_text

    if getattr(workspace, "kind", "") not in {"organization", "personal"}:
        return [], []
    context = get_default_context()
    inventories = []
    for model in (App, Track):
        rows = await context.database.find(
            "node",
            {
                "entity": model._entity_name(),
                "$or": [
                    {"context.workspace_id": workspace.id},
                    {"workspace_id": workspace.id},
                ],
            },
            limit=10001,
        )
        if len(rows) > 10000 or len({row.get("id") for row in rows}) != len(rows):
            raise AuthorizationUnavailableError()
        candidates = []
        for row in rows:
            if (
                not isinstance(row.get("id"), str)
                or not row["id"]
                or row.get("entity") != model._entity_name()
                or not isinstance(row.get("context"), dict)
                or required_node_text(row, "workspace_id") != workspace.id
            ):
                raise AuthorizationUnavailableError()
            candidate = await context._deserialize_entity(model, row)
            if candidate is None:
                raise AuthorizationUnavailableError()
            candidates.append(candidate)
        inventories.append(candidates)
    return inventories[0], inventories[1]


async def collect_org_workspace_member_visibility_inventory(
    workspace: Workspace,
) -> Tuple[list[App], list[Track]]:
    """Apps/tracks with non-private effective visibility in an org workspace.

    Seeds member list queries; ``resolve_role`` confirms each candidate.
    Guests are excluded at resolution time (visibility cascade requires
    non-guest ``IS_MEMBER_OF``).
    """
    if getattr(workspace, "kind", "") != "organization":
        return [], []

    from app.services.permissions import effective_resource_visibility

    apps_by_id: dict[str, App] = {}
    tracks_by_id: dict[str, Track] = {}

    for app in await App.find({"workspace_id": workspace.id}):
        if await effective_resource_visibility("app", app) != "private":
            apps_by_id[app.id] = app

    for track in await Track.find({"workspace_id": workspace.id}):
        if await effective_resource_visibility("track", track) != "private":
            tracks_by_id[track.id] = track

    return list(apps_by_id.values()), list(tracks_by_id.values())


async def count_owned_personal_workspaces(user_id: str) -> int:
    """Count personal-kind workspaces the user owns (``IS_MEMBER_OF`` owner).

    Used by workspace delete to enforce "keep at least one personal workspace".
    """
    accessible = await list_accessible_workspaces(user_id)
    n = 0
    for ws in accessible:
        if getattr(ws, "kind", "") != "personal":
            continue
        if await is_workspace_owner(user_id, ws.id):
            n += 1
    return n


async def list_accessible_workspaces(user_id: str) -> list[Workspace]:
    """All workspaces the user can read.

    Personal workspace of the user + every organization-kind workspace
    the user has an IS_MEMBER_OF edge to. Used by ``/api/workspaces``
    list endpoint and by accessible-apps/tracks traversal in W5.
    """
    from app.services.permissions import get_user_node

    try:
        with isolated_permissions_cache():
            user = await get_user_node(user_id)
            if not user:
                return []
            return await _list_current_accessible_workspaces(user)
    except Exception as exc:
        logger.exception("list_accessible_workspaces traversal failed")
        raise AuthorizationUnavailableError() from exc


async def _list_current_accessible_workspaces(
    user: User, *, roles_out: dict[str, str] | None = None
) -> list[Workspace]:
    """Continue one admitted principal evaluation; never cache its result."""
    out: dict[str, Workspace] = {}
    cursor = None
    seen_cursors = set()
    while True:
        page, cursor = await user.nodes_page(
            edge=[IS_MEMBER_OF],
            node=[Workspace],
            direction="out",
            limit=200,
            cursor=cursor,
        )
        for candidate in page:
            ws = await Workspace.find_one({"id": candidate.id})
            role = await _current_workspace_role(user, ws) if ws is not None else "none"
            if ws is not None and role != "none":
                out[ws.id] = ws
                if roles_out is not None:
                    roles_out[ws.id] = role
        if not cursor:
            return list(out.values())
        if cursor in seen_cursors:
            raise AuthorizationUnavailableError()
        seen_cursors.add(cursor)
