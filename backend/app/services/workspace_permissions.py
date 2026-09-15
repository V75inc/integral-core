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

from app.models.edges import IS_MEMBER_OF
from app.models.nodes import App, Track, User, Workspace

logger = logging.getLogger(__name__)

WorkspaceRole = Literal["owner", "admin", "member", "guest", "none"]


async def _read_member_edge_role(user: User, workspace_id: str) -> Optional[str]:
    """Return the role stored on the IS_MEMBER_OF edge, or None."""
    try:
        ctx = await user.get_context()
        rows = await ctx.database.find(
            "edge",
            {
                "source": user.id,
                "target": workspace_id,
                "entity": IS_MEMBER_OF.__name__,
            },
        )
        if not rows:
            return None
        row = rows[0]
        row_ctx = row.get("context") if isinstance(row, dict) else None
        role = (row_ctx.get("role") if isinstance(row_ctx, dict) else None) or row.get(
            "role"
        )
        return str(role or "").strip().lower() or "member"
    except Exception:
        logger.exception(
            "_read_member_edge_role failed for %s → %s", user.id, workspace_id
        )
        return None


async def get_workspace_owner_user_id(workspace_id: str) -> Optional[str]:
    """Resolve the owner User node id via ``IS_MEMBER_OF{role:"owner"}``.

    Works uniformly for Personal (sole member) and Organization (any
    member with role="owner"). Returns ``None`` if the workspace has no
    owner edge — a structural invariant violation that callers may
    treat as an access-denied condition.
    """
    ws = await Workspace.get(workspace_id)
    if not ws:
        return None
    try:
        ctx = await ws.get_context()
        rows = await ctx.database.find(
            "edge",
            {"target": workspace_id, "entity": IS_MEMBER_OF.__name__},
        )
        for row in rows:
            row_ctx = row.get("context") if isinstance(row, dict) else None
            role = (
                row_ctx.get("role") if isinstance(row_ctx, dict) else None
            ) or row.get("role")
            if str(role or "").strip().lower() == "owner":
                source = row.get("source")
                if source:
                    return str(source)
    except Exception:
        logger.exception("get_workspace_owner_user_id failed for %s", workspace_id)
    return None


async def can_access_workspace(user_id: str, workspace_id: str) -> WorkspaceRole:
    """Return the user's role within the given Workspace, or "none".

    Edge-only resolution: reads ``IS_MEMBER_OF`` role for both kinds.
    Returns one of {owner, admin, member, guest} or "none".
    """
    from app.services.permissions import get_user_node

    user = await get_user_node(user_id)
    if not user:
        return "none"
    ws = await Workspace.get(workspace_id)
    if not ws:
        return "none"
    role = await _read_member_edge_role(user, ws.id)
    if role in ("owner", "admin", "member", "guest"):
        return role  # type: ignore[return-value]
    return "none"


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
    owner/admin staff need inventory visibility and operational access to
    contained Apps/Tracks without a per-resource ``COLLABORATES_ON`` edge.
    """
    ws = await Workspace.get(workspace_id)
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
    """Active apps + all tracks for org workspace owner/admin inventory.

    Returns catalogued active apps, standalone tracks from the workspace
    Tracks branch, and tracks ``CONTAINS``-ed under each app. Deduped by id.

    Apps are unioned from the workspace Apps branch registry **and**
    ``App.find(workspace_id=...)`` so legacy or partially-provisioned installs
    (track catalogued but app branch edge missing) still appear in inventory.
    """
    if getattr(workspace, "kind", "") != "organization":
        return [], []

    from app.services.workspace_lifecycle import _collect_contained

    apps_by_id: dict[str, App] = {}

    for app in await _collect_contained(workspace, kind="apps"):
        if str(getattr(app, "lifecycle_state", "") or "active") == "active":
            apps_by_id[app.id] = app

    for app in await App.find({"workspace_id": workspace.id}):
        if str(getattr(app, "lifecycle_state", "") or "active") != "active":
            continue
        apps_by_id.setdefault(app.id, app)

    apps = list(apps_by_id.values())

    tracks_by_id: dict[str, Track] = {}
    for track in await _collect_contained(workspace, kind="tracks"):
        tracks_by_id[track.id] = track
    for app in apps:
        for track in await app.nodes(edge=["CONTAINS"], node=["Track"]):
            tracks_by_id[track.id] = track

    return apps, list(tracks_by_id.values())


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
        if str(getattr(app, "lifecycle_state", "") or "active") != "active":
            continue
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
    from app.services.personal_workspace import _find_personal_workspace

    user = await get_user_node(user_id)
    if not user:
        return []

    out: dict[str, Workspace] = {}

    # Personal workspace must be provisioned at signup (auth.py::signup).
    # List does not lazy-create — legacy users without one are invisible
    # until backfilled via ensure_personal_workspace migration script.
    personal = await _find_personal_workspace(user)
    if personal:
        out[personal.id] = personal

    # Organization-kind workspaces via IS_MEMBER_OF.
    try:
        member_workspaces = await user.nodes(edge=["IS_MEMBER_OF"], node=["Workspace"])
        for ws in member_workspaces:
            if ws.id not in out:
                out[ws.id] = ws
    except Exception:
        logger.exception("list_accessible_workspaces traversal failed")

    return list(out.values())
