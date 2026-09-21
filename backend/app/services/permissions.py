"""Permission checking service implementing Integral's access model.

Roles: ``owner | admin | editor | commenter | viewer``. Single resolver
(``resolve_role``) walks the parent chain Entry → Track → App, honouring
per-user ``EXCLUDED_FROM`` edges and App/Track ``visibility`` grants
(ARCHITECTURE §9.5). Workspace ``IS_MEMBER_OF`` membership does **not**
implicitly cascade to all children — non-guest members reach
workspace-visible resources via the visibility grant; guests require
explicit ``COLLABORATES_ON`` edges.

Resolution priority (highest wins; deny on this resource blocks inheritance
only — direct grants beat exclusion):

  1. Personal-workspace owner short-circuit (workspace resource).
  2. Workspace membership gate for app/track/entry resources.
  3. Direct ``OWNS`` edge → "owner".
  4. Direct ``COLLABORATES_ON`` edge → that role.
  5. ``EXCLUDED_FROM`` on this resource → None (blocks inherited paths).
  6. Org workspace owner/admin staff implicit role on app/track.
  7. ``visibility`` grant (workspace → non-guest members; public → any user).
  8. Recurse on parent; cap any inherited role above "editor" to "editor".
     Track ``visibility="private"`` opts out of visibility/staff-only parent
     cascade; direct ``OWNS`` / ``COLLABORATES_ON`` on the parent App still
     cascade (``EXCLUDED_FROM`` is the per-user override).

The ``can_view_X`` / ``can_edit_X`` / ``can_delete_X`` helpers are thin
wrappers over ``resolve_role``.

DEPRECATED (Plan 03-02) — direct callers under ``app/api/`` and
``app/agentive/api/`` have been migrated to
``app.services.policy_engine.evaluate(...)``. The ``can_*`` helpers in this
module remain because the engine's default-human path delegates back to
them (``_evaluate_human_default`` in ``policy_engine.py``). The CI grep gate
at ``backend/tests/test_no_legacy_helper_calls.py`` enforces zero direct
calls from controller-layer code. Phase 7 cleanup will inline the
precedence logic into ``policy_engine.py`` and delete the wrappers.
"""

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from app.middleware.permissions_cache import permissions_cache_get
from app.models.edges import COLLABORATES_ON, EXCLUDED_FROM, IS_MEMBER_OF, OWNS
from app.models.nodes import App, Entry, Track, User, View, Workspace
from app.services.perf_trace import perf_traced

logger = logging.getLogger(__name__)


def _permission_memo_enabled() -> bool:
    """Skip memoization under pytest so graph mutations in the same test see fresh roles."""
    return not (os.getenv("TESTING") or os.getenv("PYTEST_CURRENT_TEST"))


try:
    from jvspatial.api.auth.models import User as AuthUser
except ImportError:
    # mypy: reassigning an imported class name to None is the canonical
    # optional-import fallback pattern; the type checker can't express
    # "this name is a class or None" here without a redefinition.
    AuthUser = None  # type: ignore[misc]


async def get_user_node(user_id: str) -> Optional[User]:
    """Resolve a User graph node from User id or AuthUser id."""
    cache = permissions_cache_get() if _permission_memo_enabled() else None
    ck = ("user_node", user_id)
    if cache is not None and ck in cache:
        return cache[ck]

    user = await User.get(user_id)
    if user:
        if cache is not None:
            cache[ck] = user
        return user

    if not AuthUser:
        if cache is not None:
            cache[ck] = None
        return None

    try:
        auth_user = await AuthUser.get(user_id)
        if not auth_user:
            if cache is not None:
                cache[ck] = None
            return None

        nodes = await User.find({"context.user_id": auth_user.id})
        if not nodes:
            nodes = await User.find({"user_id": auth_user.id})
        user = nodes[0] if nodes else None
        if cache is not None:
            cache[ck] = user
        return user
    except Exception as exc:
        logger.warning("Failed to resolve User for %s: %s", user_id, exc)
        if cache is not None:
            cache[ck] = None
        return None


async def batch_resolve_users_by_principal_ids(
    principal_ids: List[str],
) -> Dict[str, User]:
    """Map principal ids (User node id or AuthUser id) to User nodes.

    ``author_id``, ``owner_user_id``, and similar fields may store either
    the graph User node id (``n.User.*``) or the AuthUser principal id
    (``o.User.*``) depending on which layer wrote the record. The returned
    map is keyed by every id form encountered so callers can look up with
    the verbatim value on the record.
    """
    ids = list({str(pid).strip() for pid in principal_ids if pid and str(pid).strip()})
    if not ids:
        return {}

    from app.services.graph_hydration import batch_get_by_ids

    result: Dict[str, User] = {}
    unmatched = set(ids)

    by_node_id_map = await batch_get_by_ids(User, ids)
    for node_id_s, user in by_node_id_map.items():
        # The graph ``User`` Node and jvspatial's ``AuthUser`` Object both
        # persist under the ``"User"`` discriminator, so an ``o.User.*``
        # principal id resolves to the AuthUser record here. Accepting it would
        # short-circuit the ``context.user_id`` pass below that finds the real
        # profile node — leaving callers with a record that has no
        # ``display_name`` (and, before ``public_user_view``, one that leaked
        # ``password_hash``). Keep graph nodes only; let the rest fall through.
        if not isinstance(user, User):
            continue
        result[node_id_s] = user
        unmatched.discard(node_id_s)
        auth_id = (getattr(user, "user_id", None) or "").strip()
        if auth_id:
            result[auth_id] = user
            unmatched.discard(auth_id)

    if unmatched:
        remaining = list(unmatched)
        by_auth = await User.find({"user_id": {"$in": remaining}})
        for user in by_auth or []:
            node_id = getattr(user, "id", None)
            auth_id = (getattr(user, "user_id", None) or "").strip()
            if auth_id:
                result[auth_id] = user
                unmatched.discard(auth_id)
            if node_id:
                node_id_s = str(node_id)
                result[node_id_s] = user
                unmatched.discard(node_id_s)

    if unmatched:
        remaining = list(unmatched)
        by_ctx = await User.find({"context.user_id": {"$in": remaining}})
        for user in by_ctx or []:
            node_id = getattr(user, "id", None)
            auth_id = (getattr(user, "user_id", None) or "").strip()
            ctx_auth = ""
            ctx = getattr(user, "context", None) or {}
            if isinstance(ctx, dict):
                ctx_auth = str(ctx.get("user_id") or "").strip()
            for pid in (auth_id, ctx_auth, str(node_id) if node_id else ""):
                if pid:
                    result[pid] = user
                    unmatched.discard(pid)

    return result


def member_edge_bool(edge, key: str) -> bool:
    """Read boolean flag from edge model or persisted context."""
    v = getattr(edge, key, None)
    if v is not None:
        return bool(v)
    ctx = getattr(edge, "context", None) or {}
    return bool(ctx.get(key))


# --- Workspace creation gates ---


async def _is_workspace_owner_user(workspace: Workspace, user: User) -> bool:
    """True iff ``user`` holds the ``IS_MEMBER_OF{role:"owner"}`` edge.

    Uniform for both Personal and Organization — a Personal workspace
    always has exactly one such edge to its owning User.
    """
    try:
        ctx = await user.get_context()
        edges = await ctx.find_edges_between(
            user.id, workspace.id, edge_class=IS_MEMBER_OF
        )
        for e in edges:
            role = getattr(e, "role", None)
            if role is None:
                rctx = getattr(e, "context", None) or {}
                role = rctx.get("role") if isinstance(rctx, dict) else None
            if str(role or "").strip().lower() == "owner":
                return True
    except Exception:
        logger.exception("workspace owner check failed")
    return False


async def can_create_app_under_workspace(user_id: str, workspace_id: str) -> bool:
    """Owner — or any member with the ``can_create_apps`` grant — may create Apps.

    App/Track creation is a selective, explicitly-granted capability carried on
    the ``IS_MEMBER_OF`` edge (I-ROLE / "authority requires explicit grant",
    9ffd952). The admin role governs the member pool + settings, NOT creation,
    so an admin without the grant is denied here — matching the listing flags
    in ``api/workspaces._caller_member_creation_flags`` and the
    ``can_publish_operational_models_under_workspace`` gate below.
    """
    user = await get_user_node(user_id)
    if not user:
        return False
    ws = await Workspace.get(workspace_id)
    if not ws:
        return False
    if await _is_workspace_owner_user(ws, user):
        return True
    ctx = await user.get_context()
    edges = await ctx.find_edges_between(user.id, workspace_id, edge_class=IS_MEMBER_OF)
    if not edges:
        return False
    return member_edge_bool(edges[0], "can_create_apps")


async def can_create_track_under_workspace(user_id: str, workspace_id: str) -> bool:
    """Owner — or any member with the ``can_create_tracks`` grant — may create Tracks.

    Selective, explicitly-granted capability (see
    ``can_create_app_under_workspace``): the admin role does not auto-grant
    track creation; the ``IS_MEMBER_OF`` edge flag is authoritative.
    """
    user = await get_user_node(user_id)
    if not user:
        return False
    ws = await Workspace.get(workspace_id)
    if not ws:
        return False
    if await _is_workspace_owner_user(ws, user):
        return True
    ctx = await user.get_context()
    edges = await ctx.find_edges_between(user.id, workspace_id, edge_class=IS_MEMBER_OF)
    if not edges:
        return False
    return member_edge_bool(edges[0], "can_create_tracks")


async def can_publish_operational_models_under_workspace(
    user_id: str, workspace_id: str
) -> bool:
    """Owner (any workspace kind) or privileged org member may publish
    workspace-scoped CP packages.

    The OWNER of a workspace — INCLUDING their personal workspace — may publish
    CP packages in it: the agent's scaffold flow authors a library Operational Model as a
    normal step, so a personal-workspace owner must be able to run it in their
    own space. Personal workspaces have a single owner and no member pool, so a
    non-owner gets nothing there; org workspaces additionally grant members
    carrying ``can_create_apps`` / ``can_create_tracks``.
    """
    user = await get_user_node(user_id)
    if not user:
        return False
    ws = await Workspace.get(workspace_id)
    if not ws:
        return False
    if await _is_workspace_owner_user(ws, user):
        return True
    if ws.kind == "personal":
        # Single owner, no member pool — only the owner (handled above) publishes.
        return False
    ctx = await user.get_context()
    edges = await ctx.find_edges_between(user.id, workspace_id, edge_class=IS_MEMBER_OF)
    if not edges:
        return False
    edge = edges[0]
    return member_edge_bool(edge, "can_create_apps") or member_edge_bool(
        edge, "can_create_tracks"
    )


async def _user_in_workspace_member_pool(user: User, workspace_id: str) -> bool:
    """True if user has any IS_MEMBER_OF edge to the workspace (incl. guest)."""
    try:
        ctx = await user.get_context()
        edges = await ctx.find_edges_between(
            user.id, workspace_id, edge_class=IS_MEMBER_OF
        )
        return bool(edges)
    except Exception as exc:
        logger.warning("workspace membership check failed: %s", exc)
    return False


# --- App-level ---


async def can_view_app(user_id: str, app_id: str) -> bool:
    """True if the user resolves to any role on the app_node.

    DEPRECATED — use ``policy_engine.evaluate(subject=Subject(kind='human', id=user_id),
    action='app.read', resource=Resource(kind='app', id=app_id, scope=f'app:{app_id}'))``.
    Retained as the engine's default-human delegation target; controller-layer
    callers must NOT invoke this directly (enforced by
    ``tests/test_no_legacy_helper_calls.py``).
    """
    app = await App.get(app_id)
    if not app or str(getattr(app, "lifecycle_state", "") or "") == "uninstalled":
        return False
    return await resolve_role(user_id, "app", app_id) is not None


def invalidate_user_accessible_caches(user_id: str) -> None:
    """Drop a user's cached ``accessible_tracks`` / ``accessible_apps`` aggregates
    from BOTH the per-request memo and the process TTL cache.

    Call this after a write that changes what the user can see — notably a track
    or app CREATE, which adds no permission edge (so the sharing invalidation
    hooks don't fire) yet must appear in the accessible set immediately. Without
    this, a staged batch that creates an app/track and then references it in a
    LATER op (e.g. create_entry with the new track_id) hits a scope check that
    reads the pre-create cached list and rejects the just-created track as
    "outside the active workspace" — the multi-app-create-fails-until-approved-
    twice bug (June 29 QA #2). Clearing both layers forces the next scope check
    in the same request to recompute against live graph state.
    """
    if not user_id:
        return
    from app.services import permissions_process_cache as _ppc

    _ppc.invalidate_user(user_id)
    cache = permissions_cache_get() if _permission_memo_enabled() else None
    if cache is not None:
        cache.pop(("accessible_tracks", user_id), None)
        cache.pop(("accessible_apps", user_id), None)


async def get_user_accessible_apps(user_id: str) -> List[App]:
    """Apps via ownership, collaboration, staff inventory, and visibility grants.

    Org workspace members additionally see apps with ``visibility`` workspace
    or public (ARCHITECTURE §9.5). Guests require explicit ``COLLABORATES_ON``.
    """
    cache = permissions_cache_get() if _permission_memo_enabled() else None
    ck = ("accessible_apps", user_id)
    if cache is not None and ck in cache:
        return cache[ck]

    from app.services import permissions_process_cache as _ppc

    _pc = _ppc.get_cached(user_id, "accessible_apps")
    if _pc is not None:
        if cache is not None:
            cache[ck] = _pc
        return _pc

    user = await get_user_node(user_id)
    if not user:
        if cache is not None:
            cache[ck] = []
        return []

    try:
        seen: set = set()
        result: List[App] = []

        def _add(sp: App) -> None:
            if str(getattr(sp, "lifecycle_state", "") or "") == "uninstalled":
                return
            if sp.id not in seen:
                seen.add(sp.id)
                result.append(sp)

        for sp in await user.nodes(edge=["OWNS"], node=["WorkspaceApp"]):
            _add(sp)
        for sp in await user.nodes(edge=["COLLABORATES_ON"], node=["WorkspaceApp"]):
            _add(sp)

        # Org workspace owner/admin: full catalogued inventory without
        # per-app COLLABORATES_ON edges (members still require explicit grants).
        from app.services.workspace_permissions import (
            can_access_workspace,
            collect_org_workspace_member_visibility_inventory,
            collect_org_workspace_staff_inventory,
            list_accessible_workspaces,
        )

        for ws in await list_accessible_workspaces(user_id):
            if getattr(ws, "kind", "") != "organization":
                continue
            ws_role = await can_access_workspace(user_id, ws.id)
            if ws_role in ("owner", "admin"):
                staff_apps, _ = await collect_org_workspace_staff_inventory(ws)
                for sp in staff_apps:
                    _add(sp)
            elif ws_role == "member":
                vis_apps, _ = await collect_org_workspace_member_visibility_inventory(
                    ws
                )
                pending_apps = [sp for sp in vis_apps if sp.id not in seen]
                if pending_apps:
                    roles = await asyncio.gather(
                        *(resolve_role(user_id, "app", sp.id) for sp in pending_apps)
                    )
                    for sp, role in zip(pending_apps, roles):
                        if role is not None:
                            _add(sp)

        if cache is not None:
            cache[ck] = result
        _ppc.set_cached(user_id, "accessible_apps", result)
        return result
    except Exception as exc:
        logger.warning("Error getting accessible apps for %s: %s", user_id, exc)
        if cache is not None:
            cache[ck] = []
        return []


async def can_edit_app(user_id: str, app_id: str) -> bool:
    """True if the user is an owner, admin, or editor on the app_node.

    "editor" = entry CRUD authority (within Tracks under this App).
    "admin"  = entry CRUD + App-config authority (schema/views/tags
               cascade via ``DEFINES_TRACK_PROFILE``).

    DEPRECATED — use ``policy_engine.evaluate(action='app.update', ...)``.
    See ``can_view_app`` docstring for the migration contract.
    """
    return await resolve_role(user_id, "app", app_id) in ("owner", "admin", "editor")


async def can_admin_app(user_id: str, app_id: str) -> bool:
    """True if the user is owner or admin on the App.

    Gates App-config authority: attached OperationalModel mutations,
    template-track edits, library-derivation. NOT editor-accessible —
    editors get entry CRUD only.
    """
    return await resolve_role(user_id, "app", app_id) in ("owner", "admin")


async def can_delete_app(user_id: str, app_id: str) -> bool:
    """True if the user owns the app_node.

    DEPRECATED — use ``policy_engine.evaluate(action='app.delete', ...)``.
    See ``can_view_app`` docstring for the migration contract.
    """
    return await resolve_role(user_id, "app", app_id) == "owner"


# --- Track-level ---


async def can_view_track(user_id: str, track_id: str) -> bool:
    """True if the user resolves to any effective track role.

    DEPRECATED — use ``policy_engine.evaluate(action='track.read', ...)``. See
    ``can_view_app`` docstring for the migration contract.
    """
    track = await Track.get(track_id)
    if not track:
        return False
    try:
        parent_apps = await track.nodes(
            edge=["CONTAINS"], direction="in", node=["WorkspaceApp"]
        )
        if parent_apps and all(
            str(getattr(app, "lifecycle_state", "") or "") == "uninstalled"
            for app in parent_apps
        ):
            return False
    except Exception as exc:
        logger.warning("track parent app lookup failed for %s: %s", track_id, exc)
        return False
    return await resolve_role(user_id, "track", track_id) is not None


async def can_edit_track(user_id: str, track_id: str) -> bool:
    """True if the user is owner, admin, or editor on the track.

    "editor"  = entry CRUD authority (no track-config rights).
    "admin"   = entry CRUD + track-config (schema/views/tags/library).

    This helper now gates entry CRUD ONLY. Track-config endpoints (entry
    types, views, tags mutations, operational-model, anchors, migrations)
    moved to ``can_admin_track`` via ``policy_engine.evaluate(action='track.update')``.

    DEPRECATED — use ``policy_engine.evaluate(action='entry.create', ...)``
    for entry-creation gating or ``can_admin_track`` for config gating.
    """
    return await resolve_role(user_id, "track", track_id) in (
        "owner",
        "admin",
        "editor",
    )


async def can_admin_track(user_id: str, track_id: str) -> bool:
    """True if the user is owner or admin on the track.

    Gates track-config authority (schema, views, tags, library, anchors,
    migrations, default view, detach/revert). Editors do NOT pass — they
    get entry CRUD only.
    """
    return await resolve_role(user_id, "track", track_id) in ("owner", "admin")


async def can_delete_track(user_id: str, track_id: str) -> bool:
    """True if the user owns the track.

    DEPRECATED — use ``policy_engine.evaluate(action='track.delete', ...)``.
    See ``can_view_app`` docstring for the migration contract.
    """
    return await resolve_role(user_id, "track", track_id) == "owner"


# --- Entry-level ---


async def can_view_entry(user_id: str, entry_id: str) -> bool:
    """True if the user may read the entry.

    Resolves through the parent Track, honouring per-entry visibility opt-out
    and per-user exclusion. Entries follow track access only — no author
    shortcut at view time (rule 5 + AGENTS.md "no per-entry ACL" baseline).

    DEPRECATED — use ``policy_engine.evaluate(action='entry.read', ...)``. See
    ``can_view_app`` docstring for the migration contract.
    """
    return await resolve_role(user_id, "entry", entry_id) is not None


async def can_edit_entry(user_id: str, entry_id: str) -> bool:
    """True if the user is the author or can edit via cascade / direct grant.

    Roles owner / admin / editor all carry entry-CRUD authority on the
    parent track and therefore beat the per-entry role check. commenter /
    viewer fall back to the author shortcut (own entries only).

    DEPRECATED — use ``policy_engine.evaluate(action='entry.update', ...)``.
    See ``can_view_app`` docstring for the migration contract.
    """
    role = await resolve_role(user_id, "entry", entry_id)
    if role in ("owner", "admin", "editor"):
        return True
    # EXCLUDED_FROM / no grant → resolve_role is None; do not let author shortcut
    # resurrect edit after an explicit deny.
    if role is None:
        return False
    try:
        entry = await Entry.get(entry_id)
        return bool(entry and entry.author_id == user_id)
    except Exception as exc:
        logger.warning("Error checking entry edit permission for %s: %s", user_id, exc)
        return False


# --- View-level ---


async def can_view_view(user_id: str, view_id: str) -> bool:
    """True if the user can view the view's track.

    Per-track views (``track_id`` set) resolve against that track. Template
    rows with empty ``track_id`` (shared by-reference CP materialization) are
    not directly readable via this helper — callers list track-scoped copies.
    Do **not** OR across every track sharing the OperationalModel; that over-grants
    under shared-CP-by-reference.

    DEPRECATED — use ``policy_engine.evaluate(action='view.read', ...)``. See
    ``can_view_app`` docstring for the migration contract.
    """
    try:
        view = await View.get(view_id)
        if not view:
            return False
        track_id = str(getattr(view, "track_id", "") or "").strip()
        if not track_id:
            return False
        return await can_view_track(user_id, track_id)
    except Exception as exc:
        logger.warning("Error checking view permission for %s: %s", user_id, exc)
        return False


async def can_edit_view(user_id: str, view_id: str) -> bool:
    """True if the user has track-admin authority on the view's track.

    Views are track-config substrate, so editing requires admin / owner —
    not editor. Editors may save personal view configs via a future
    per-user-view surface, but cannot mutate shared track views.

    Per-track views carry ``track_id``. Template rows (``track_id`` empty) are
    refused — mutate the per-track copy, not the shared template.

    DEPRECATED — use ``policy_engine.evaluate(action='view.update', ...)``. See
    ``can_view_app`` docstring for the migration contract.
    """
    try:
        view = await View.get(view_id)
        if not view:
            return False
        track_id = str(getattr(view, "track_id", "") or "").strip()
        if not track_id:
            return False
        return await can_admin_track(user_id, track_id)
    except Exception as exc:
        logger.warning("Error checking view edit permission for %s: %s", user_id, exc)
        return False


# --- Unified resolver ---
#
# Single source of truth for "what role does user X have on resource Y?".
# Walks the parent chain (Entry → Track → App) honouring visibility grants,
# per-resource visibility opt-outs, and per-level EXCLUDED_FROM edges.

ROLE_RANK = {"owner": 5, "admin": 4, "editor": 3, "commenter": 2, "viewer": 1}

_VISIBILITY_ALIASES = {"organization": "workspace"}


def canonicalize_visibility(value: Optional[str]) -> str:
    """Normalize stored visibility; legacy ``organization`` → ``workspace``."""
    raw = str(value or "").strip().lower()
    if not raw or raw == "inherit":
        return "inherit"
    return _VISIBILITY_ALIASES.get(raw, raw)


async def effective_resource_visibility(resource_type: str, node: Any) -> str:
    """Resolved visibility for access grants: ``private | workspace | public``.

    Track ``inherit`` walks the parent App via ``CONTAINS`` in; standalone
    tracks with ``inherit`` and no parent App resolve to ``private``.
    """
    if resource_type == "app":
        vis = canonicalize_visibility(getattr(node, "visibility", None))
        if vis == "inherit":
            return "private"
        return vis if vis in ("private", "workspace", "public") else "private"

    if resource_type == "track":
        vis = canonicalize_visibility(getattr(node, "visibility", None))
        if vis not in ("", "inherit"):
            return vis if vis in ("private", "workspace", "public") else "private"
        try:
            apps = await node.nodes(
                edge=["CONTAINS"], direction="in", node=["WorkspaceApp"]
            )
            if apps:
                return await effective_resource_visibility("app", apps[0])
        except Exception as exc:
            logger.warning(
                "effective_resource_visibility parent lookup failed for track %s: %s",
                getattr(node, "id", "?"),
                exc,
            )
        return "private"

    return "private"


async def _is_publicly_readable_resource(resource_type: str, node: Any) -> bool:
    """True when ARCHITECTURE §9.5 step 5 grants any authenticated user read."""
    if resource_type in ("app", "track"):
        return await effective_resource_visibility(resource_type, node) == "public"
    if resource_type == "entry":
        track_id = getattr(node, "track_id", "") or ""
        if not track_id:
            try:
                tracks = await node.nodes(
                    edge=["CONTAINS"], direction="in", node=["Track"]
                )
                if tracks:
                    track_id = tracks[0].id
            except Exception:
                return False
        if not track_id:
            return False
        track = await Track.get(track_id)
        if not track:
            return False
        return await effective_resource_visibility("track", track) == "public"
    return False


async def _visibility_grant_role(
    user_id: str, resource_type: str, node: Any
) -> Optional[str]:
    """ARCHITECTURE §9.5 step 5: visibility-based read grant, or None."""
    if resource_type not in ("app", "track"):
        return None

    vis = await effective_resource_visibility(resource_type, node)
    if vis == "private":
        return None
    if vis == "public":
        return "viewer"
    if vis == "workspace":
        ws_id = await _workspace_id_for_resource(resource_type, node)
        if not ws_id:
            return None
        ws = await Workspace.get(ws_id)
        if not ws or getattr(ws, "kind", "") != "organization":
            return None
        from app.services.workspace_permissions import user_in_workspace_cascade_view

        if not await user_in_workspace_cascade_view(user_id, ws_id):
            return None
        return "viewer"
    return None


def _cap_inherited_role(role: str) -> str:
    """Cascade demotion: any inherited role above "editor" caps to "editor".

    Track-config authority (schema, views, tags, library) is a per-resource
    decision and MUST be granted directly on the resource — never via
    parent cascade. App owners / admins inheriting onto contained Tracks
    get entry-CRUD authority ("editor") but cannot curate per-track
    substrate; an App owner who wants to reshape a specific Track's
    schema must add themselves (or a delegate) as a direct admin on that
    Track via the collaborators surface.

    Rationale (I-ROLE-02): inherited owner / admin would silently grant
    schema-mutation authority across every Track in an App, making the
    admin tier meaningless at the App-cascade boundary. Capping at
    "editor" preserves the cascade as a productivity affordance
    (collaborators added at the App level can still create / edit entries
    everywhere) without leaking curation authority. Owner-only carve-outs
    (delete / collab mgmt / share-link mint) are likewise never inherited.
    """
    return "editor" if role in ("owner", "admin") else role


async def _none() -> None:
    """Awaitable ``None`` — placeholder in an ``asyncio.gather`` whose other
    branch is conditional, so the gather shape stays fixed."""
    return None


async def _direct_role_on_resource(user: User, resource_id: str) -> Optional[str]:
    """Direct OWNS or COLLABORATES_ON role on this exact resource, or None."""
    try:
        ctx = await user.get_context()
        owns_edges = await ctx.find_edges_between(user.id, resource_id, edge_class=OWNS)
        if owns_edges:
            return "owner"
        collab_edges = await ctx.find_edges_between(
            user.id, resource_id, edge_class=COLLABORATES_ON
        )
        if not collab_edges:
            return None
        edge = collab_edges[0]
        role_val = getattr(edge, "role", None)
        if role_val is None:
            rctx = getattr(edge, "context", None) or {}
            role_val = rctx.get("role") if isinstance(rctx, dict) else None
        cand = str(role_val or "viewer").strip().lower()
        return cand if cand in ROLE_RANK else "viewer"
    except Exception as exc:
        logger.warning(
            "Error reading direct role on %s for %s: %s", resource_id, user.id, exc
        )
        return None


async def _is_excluded_from_resource(user: User, resource_id: str) -> bool:
    """True iff an EXCLUDED_FROM edge exists from this user to this resource."""
    try:
        ctx = await user.get_context()
        edges = await ctx.find_edges_between(
            user.id, resource_id, edge_class=EXCLUDED_FROM
        )
        return bool(edges)
    except Exception as exc:
        logger.warning(
            "Error reading exclusion on %s for %s: %s", resource_id, user.id, exc
        )
        return False


async def _parent_of_resource(resource_type: str, resource_id: str) -> Optional[tuple]:
    """Return (parent_type, parent_id) or None if the resource has no parent."""
    try:
        if resource_type == "entry":
            entry = await Entry.get(resource_id)
            if not entry:
                return None
            if entry.track_id:
                return ("track", entry.track_id)
            tracks = await entry.nodes(
                edge=["CONTAINS"], direction="in", node=["Track"]
            )
            if tracks:
                return ("track", tracks[0].id)
            return None
        if resource_type == "track":
            track = await Track.get(resource_id)
            if not track:
                return None
            apps = await track.nodes(
                edge=["CONTAINS"], direction="in", node=["WorkspaceApp"]
            )
            if apps:
                return ("app", apps[0].id)
            return None
        # App and Workspace have no inherited-access parent (workspace
        # membership does not cascade).
        return None
    except Exception as exc:
        logger.warning(
            "Error finding parent of %s %s: %s", resource_type, resource_id, exc
        )
        return None


async def _load_resource_node(resource_type: str, resource_id: str) -> Any:
    if resource_type == "workspace":
        return await Workspace.get(resource_id)
    if resource_type == "app":
        return await App.get(resource_id)
    if resource_type == "track":
        return await Track.get(resource_id)
    if resource_type == "entry":
        return await Entry.get(resource_id)
    return None


async def _workspace_id_for_resource(resource_type: str, node: Any) -> Optional[str]:
    """Best-effort workspace anchor lookup for app/track/entry resources."""
    try:
        if resource_type in ("app", "track"):
            workspace_id = getattr(node, "workspace_id", "") or ""
            return workspace_id or None
        if resource_type == "entry":
            track_id = getattr(node, "track_id", "") or ""
            if not track_id:
                tracks = await node.nodes(
                    edge=["CONTAINS"], direction="in", node=["Track"]
                )
                if tracks:
                    track_id = tracks[0].id
            if not track_id:
                return None
            track = await Track.get(track_id)
            if not track:
                return None
            workspace_id = getattr(track, "workspace_id", "") or ""
            return workspace_id or None
    except Exception as exc:
        logger.warning("workspace anchor lookup failed for %s: %s", resource_type, exc)
    return None


async def resolve_role(
    user_id: str,
    resource_type: str,
    resource_id: str,
) -> Optional[str]:
    """Return the user's effective role on a resource, or None.

    Resolution order (highest wins; deny blocks inheritance only):

      1. Personal-workspace owner short-circuit (workspace only).
      2. Workspace membership gate for app/track/entry resources.
      3. Direct OWNS edge on the resource → "owner".
      4. Direct COLLABORATES_ON edge → that role.
      5. EXCLUDED_FROM on this resource → None (blocks inherited paths).
      6. Among remaining candidates, strongest wins (ROLE_RANK):
         org staff implicit role, visibility grant (viewer floor), and
         parent cascade (Entry→Track, Track→App; inherited roles above
         "editor" capped to "editor"). Visibility alone must not eclipse
         a stronger App/Track collaborator cascade.

    Resource types: "workspace" | "app" | "track" | "entry".
    Role values:    "owner" | "admin" | "editor" | "commenter" | "viewer" | None.
    """
    if resource_type not in ("workspace", "app", "track", "entry"):
        return None

    cache = permissions_cache_get() if _permission_memo_enabled() else None
    ck = ("rr", resource_type, resource_id, user_id)
    if cache is not None and ck in cache:
        return cache[ck]

    from app.services import permissions_process_cache as _ppc

    if _ppc.enabled():
        proc_hit = _ppc.get_resolve_role_cached(user_id, resource_type, resource_id)
        if proc_hit is not _ppc._ROLE_CACHE_MISS:
            if cache is not None:
                cache[ck] = proc_hit
            return proc_hit

    def _finish(role: Optional[str]) -> Optional[str]:
        if cache is not None:
            cache[ck] = role
        if _ppc.enabled():
            _ppc.set_resolve_role_cached(user_id, resource_type, resource_id, role)
        return role

    user = await get_user_node(user_id)
    if not user:
        return _finish(None)

    node = await _load_resource_node(resource_type, resource_id)
    if node is None:
        return _finish(None)

    if resource_type == "workspace":
        # Edge-only resolution: read IS_MEMBER_OF for both kinds; map
        # workApp role onto the owner/editor/commenter/viewer enum so
        # callers can use a single gate. WorkApp role does NOT cascade
        # to children (rule 2).
        try:
            ctx = await user.get_context()
            edges = await ctx.find_edges_between(
                user.id, resource_id, edge_class=IS_MEMBER_OF
            )
            for edge in edges:
                role_val = getattr(edge, "role", None)
                if role_val is None:
                    rctx = getattr(edge, "context", None) or {}
                    role_val = rctx.get("role") if isinstance(rctx, dict) else None
                rv = str(role_val or "").strip().lower()
                if rv == "owner":
                    out = "owner"
                elif rv in ("admin", "member"):
                    out = "editor"
                elif rv == "guest":
                    out = "viewer"
                else:
                    continue
                return _finish(out)
        except Exception as exc:
            logger.warning(
                "Error reading workspace membership for %s: %s", user.id, exc
            )
        return _finish(None)

    # deviation: procedural cascade rather than a Walker. AGENTS.md names
    # Workspace->App->Track->Entry as the textbook Walker case, so this owes a
    # measurement. Taken cold (TESTING=1 disables the process cache, so these
    # are pure cascade costs) via tests/test_resolve_role_bench.py on the
    # JsonDB fixture, before -> after this change:
    #
    #   workspace  7.7 -> 6.3ms
    #   app       10.0 -> 9.2ms
    #   track      9.4 -> 9.0ms
    #   entry     24.8 -> 23.5ms   (deepest: full three-level cascade)
    #
    # Single-run timings on a JsonDB fixture are noisy, so treat the deeper
    # deltas as indicative rather than precise. The round-trip saving below is
    # structurally certain regardless of timing.
    #
    # A Walker does NOT address the remaining cost. The expense is not graph
    # hops — the chain is at most three levels and each level short-circuits —
    # it is the heterogeneous per-level lookups a Walker would still have to
    # issue: workspace membership, EXCLUDED_FROM, visibility, the
    # staff-implicit role, and the inherited-role cap. Restructuring who issues
    # them changes the shape, not the count. Revisit if the cascade ever grows
    # beyond a bounded parent chain, where a Walker's frontier batching and
    # neighbour prefetch would start to pay.
    #
    # What the measurement did expose, and what changed here: `direct` and the
    # workspace anchor are independent and now run concurrently, and the anchor
    # is resolved once instead of twice — the candidates block below used to
    # recompute the same value with a second query.
    direct, workspace_id = await asyncio.gather(
        _direct_role_on_resource(user, resource_id),
        _workspace_id_for_resource(resource_type, node),
    )
    if workspace_id and not await _user_in_workspace_member_pool(user, workspace_id):
        ws = await Workspace.get(workspace_id)
        # A workspace OWNER always reaches resources in their workspace —
        # personal workspaces have no member pool, and a collaborative owner
        # would normally be in the pool anyway. Kind-agnostic by design.
        workspace_allowed = direct == "owner" or (
            ws is not None and await _is_workspace_owner_user(ws, user)
        )
        if not workspace_allowed:
            if not await _is_publicly_readable_resource(resource_type, node):
                return _finish(None)

    # App / Track / Entry — uniform walker (direct grant already computed).
    if direct:
        return _finish(direct)

    if await _is_excluded_from_resource(user, resource_id):
        return _finish(None)

    # Candidates from staff / visibility / parent cascade. Visibility is a
    # read floor (viewer), not a ceiling — an App editor on a workspace-
    # visible track must still resolve as editor so comment/edit gates work
    # (ARCHITECTURE §9 "strongest wins"; QA: full-permission users blocked
    # from commenting when visibility short-circuited before cascade).
    candidates: list[str] = []

    if resource_type in ("app", "track"):
        # Reuse the anchor resolved above instead of re-querying it — this was
        # a second round trip for a value already in hand.
        ws_id = workspace_id
        from app.services.workspace_permissions import (
            workspace_staff_implicit_resource_role,
        )

        # Independent of each other: the staff-implicit role reads workspace
        # membership, the visibility grant reads the resource. Run together.
        implicit, vis_grant = await asyncio.gather(
            (
                workspace_staff_implicit_resource_role(user_id, ws_id)
                if ws_id
                else _none()
            ),
            _visibility_grant_role(user_id, resource_type, node),
        )
        if implicit:
            candidates.append(implicit)

        if vis_grant:
            candidates.append(vis_grant)

    parent = await _parent_of_resource(resource_type, resource_id)
    if parent is not None:
        skip_cascade = (
            resource_type == "track"
            and await effective_resource_visibility("track", node) == "private"
            and await _direct_role_on_resource(user, parent[1]) is None
        )
        # Private tracks hide from workspace-wide visibility grants on the
        # parent App; App collaborators (direct OWNS / COLLABORATES_ON) still
        # inherit entry access. Org staff reach private inventory via the
        # implicit staff role above, not this cascade path.
        if not skip_cascade:
            inherited = await resolve_role(user_id, parent[0], parent[1])
            if inherited is not None:
                candidates.append(_cap_inherited_role(inherited))

    if not candidates:
        return _finish(None)

    strongest = max(candidates, key=lambda r: ROLE_RANK.get(r, 0))
    return _finish(strongest)


# --- Bulk queries ---


@perf_traced("permissions.get_user_accessible_tracks")
async def get_user_accessible_tracks(user_id: str) -> List[Track]:
    """Tracks via ownership, collaboration, app-cascade, staff inventory, visibility.

    All candidates are confirmed through ``resolve_role`` so per-track
    ``EXCLUDED_FROM`` edges and ``visibility=private`` opt-outs are respected.
    """
    cache = permissions_cache_get() if _permission_memo_enabled() else None
    ck = ("accessible_tracks", user_id)
    if cache is not None and ck in cache:
        return cache[ck]

    from app.services import permissions_process_cache as _ppc

    _pc = _ppc.get_cached(user_id, "accessible_tracks")
    if _pc is not None:
        if cache is not None:
            cache[ck] = _pc
        return _pc

    user = await get_user_node(user_id)
    if not user:
        if cache is not None:
            cache[ck] = []
        return []

    try:
        seen: set = set()
        result: List[Track] = []

        def _add(track: Track) -> None:
            if track.id not in seen:
                seen.add(track.id)
                result.append(track)

        async def _track_listable(track: Track) -> bool:
            """App-scoped tracks require a visible, non-uninstalled parent App."""
            template_id = str(getattr(track, "template_id", "") or "").strip()
            if not template_id:
                return True
            parent_apps = await track.nodes(
                edge=["CONTAINS"], direction="in", node=["WorkspaceApp"]
            )
            if not parent_apps:
                return True
            for app_node in parent_apps:
                lifecycle = str(getattr(app_node, "lifecycle_state", "") or "")
                if lifecycle == "uninstalled":
                    continue
                if await resolve_role(user_id, "app", app_node.id) is not None:
                    return True
            return False

        # Direct grants — bypass cascade gates when parent App is visible. The
        # per-track listability checks are independent, so resolve them
        # concurrently rather than one DB walk at a time.
        direct_tracks: List[Track] = []
        direct_tracks.extend(await user.nodes(edge=["OWNS"], node=["Track"]))
        direct_tracks.extend(await user.nodes(edge=["COLLABORATES_ON"], node=["Track"]))
        if direct_tracks:
            listable = await asyncio.gather(
                *(_track_listable(t) for t in direct_tracks)
            )
            for t, ok in zip(direct_tracks, listable):
                if ok:
                    _add(t)

        # App-cascade — must clear the per-track visibility / exclusion gate, so
        # route through resolve_role rather than blindly including. Capture each
        # candidate's parent app so we can warm the app-level role cache once,
        # then resolve every track role concurrently — each track recurses to
        # its parent app, which is now a cache hit rather than a fresh walk.
        candidate_tracks: dict = {}
        candidate_app_of: dict = {}
        cascade_apps: List[Any] = []
        cascade_apps.extend(await user.nodes(edge=["OWNS"], node=["WorkspaceApp"]))
        cascade_apps.extend(
            await user.nodes(edge=["COLLABORATES_ON"], node=["WorkspaceApp"])
        )
        active_cascade_apps = [
            ts
            for ts in cascade_apps
            if str(getattr(ts, "lifecycle_state", "") or "") != "uninstalled"
        ]
        if active_cascade_apps:
            # One bulk edge pass for all apps' contained tracks (vs one
            # traversal per app) — jvspatial Node.nodes_bulk.
            tracks_by_app = await Track.nodes_bulk(
                [ts.id for ts in active_cascade_apps],
                edge=["CONTAINS"],
                node=["Track"],
            )
            for ts in active_cascade_apps:
                for t in tracks_by_app.get(ts.id) or []:
                    candidate_tracks.setdefault(t.id, t)
                    candidate_app_of.setdefault(t.id, ts.id)
        pending = [(tid, tr) for tid, tr in candidate_tracks.items() if tid not in seen]
        if pending:
            distinct_apps = {
                candidate_app_of[tid] for tid, _ in pending if tid in candidate_app_of
            }
            if distinct_apps:
                await asyncio.gather(
                    *(resolve_role(user_id, "app", aid) for aid in distinct_apps)
                )
            roles = await asyncio.gather(
                *(resolve_role(user_id, "track", tid) for tid, _ in pending)
            )
            for (_tid, track), role in zip(pending, roles):
                if role is not None:
                    _add(track)

        from app.services.workspace_permissions import (
            can_access_workspace,
            collect_org_workspace_member_visibility_inventory,
            collect_org_workspace_staff_inventory,
            list_accessible_workspaces,
        )

        for ws in await list_accessible_workspaces(user_id):
            if getattr(ws, "kind", "") != "organization":
                continue
            ws_role = await can_access_workspace(user_id, ws.id)
            if ws_role in ("owner", "admin"):
                _, staff_tracks = await collect_org_workspace_staff_inventory(ws)
                if staff_tracks:
                    listable = await asyncio.gather(
                        *(_track_listable(t) for t in staff_tracks)
                    )
                    for t, ok in zip(staff_tracks, listable):
                        if ok:
                            _add(t)
            elif ws_role == "member":
                _, vis_tracks = await collect_org_workspace_member_visibility_inventory(
                    ws
                )
                pending_vis = [t for t in vis_tracks if t.id not in seen]
                if pending_vis:
                    listable = await asyncio.gather(
                        *(_track_listable(t) for t in pending_vis)
                    )
                    cand = [t for t, ok in zip(pending_vis, listable) if ok]
                    roles = await asyncio.gather(
                        *(resolve_role(user_id, "track", t.id) for t in cand)
                    )
                    for t, role in zip(cand, roles):
                        if role is not None:
                            _add(t)

        if cache is not None:
            cache[ck] = result
        _ppc.set_cached(user_id, "accessible_tracks", result)
        return result
    except Exception as exc:
        logger.warning("Error getting accessible tracks for %s: %s", user_id, exc)
        if cache is not None:
            cache[ck] = []
        return []


async def _collect_entries_for_tracks(
    user_id: str,
    track_ids: List[str],
    *,
    include_author_entries: bool = True,
) -> List[Entry]:
    """Gather entries for the given tracks via ``context.track_id`` (deduped).

    When ``include_author_entries`` is True (global feed / unscoped list), also include
    any entries authored by the user. App- and track-scoped calls pass False so only
    that scope's tracks contribute.
    """
    candidate_seen: set = set()
    candidates: List[Entry] = []

    if include_author_entries:
        for e in await Entry.find({"context.author_id": user_id}):
            if e.id not in candidate_seen:
                candidate_seen.add(e.id)
                candidates.append(e)

    if track_ids:
        by_prop = await Entry.find({"context.track_id": {"$in": track_ids}})
        for e in by_prop:
            if e.id not in candidate_seen:
                candidate_seen.add(e.id)
                candidates.append(e)

    return candidates


@perf_traced("permissions.get_user_accessible_entries")
async def get_user_accessible_entries(
    user_id: str,
    track_id: Optional[str] = None,
    app_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
) -> List[Entry]:
    """List entries visible to the user, optionally scoped by track or app_node.

    When ``workspace_id`` is provided, results are pruned to entries whose
    parent track (or parent app_node) resolves to that workspace. Callers that
    omit it preserve the legacy cross-workspace behaviour, so internal
    helpers and agentive code paths are unaffected.
    """
    from app.services.request_scope import (
        _effective_workspace_id_of,
        matches_workspace,
    )

    try:
        candidates: List[Entry]
        already_checked = False
        if track_id and app_id:
            app_id = None
        if app_id:
            if not await can_view_app(user_id, app_id):
                return []
            sp = await App.get(app_id)
            if not sp:
                return []
            if workspace_id and _effective_workspace_id_of(sp) != workspace_id:
                return []
            tracks = await sp.nodes(edge=["CONTAINS"], node=["Track"])
            track_checks = await asyncio.gather(
                *(can_view_track(user_id, tr.id) for tr in tracks)
            )
            visible_track_ids = [tr.id for tr, ok in zip(tracks, track_checks) if ok]
            candidates = await _collect_entries_for_tracks(
                user_id,
                visible_track_ids,
                include_author_entries=False,
            )
            already_checked = True
        elif track_id:
            if not await can_view_track(user_id, track_id):
                return []
            track = await Track.get(track_id)
            if workspace_id and (
                track is None or _effective_workspace_id_of(track) != workspace_id
            ):
                return []
            candidates = []
            candidate_seen: set = set()
            for e in await Entry.find({"context.track_id": track_id}):
                candidate_seen.add(e.id)
                candidates.append(e)
            already_checked = True
        else:
            tracks = await get_user_accessible_tracks(user_id)
            if workspace_id:
                tracks = [t for t in tracks if matches_workspace(t, workspace_id)]
            track_ids = [t.id for t in tracks]
            verified_track_set = set(track_ids)
            # When scoping to a workspace, drop the author-authored fan-out
            # in ``_collect_entries_for_tracks`` so we never pick up
            # entries whose parent track lives in another workspace.
            candidates = await _collect_entries_for_tracks(
                user_id,
                track_ids,
                include_author_entries=workspace_id is None,
            )
            already_checked = False

        if already_checked:
            return candidates

        result = []
        pending_track_checks: List[Tuple[Any, str]] = []
        for entry in candidates:
            t_id: Optional[str] = entry.track_id
            if not t_id:
                etracks = await entry.nodes(
                    edge=["CONTAINS"], direction="in", node=["Track"]
                )
                t_id = str(etracks[0].id) if etracks else None

            if not t_id:
                continue

            if workspace_id:
                if t_id in verified_track_set:
                    result.append(entry)
                continue

            if t_id in verified_track_set:
                result.append(entry)
            else:
                pending_track_checks.append((entry, t_id))

        if pending_track_checks:
            checks = await asyncio.gather(
                *(can_view_track(user_id, t_id) for _, t_id in pending_track_checks)
            )
            for (entry, _), ok in zip(pending_track_checks, checks):
                if ok:
                    result.append(entry)

        return result
    except Exception as exc:
        logger.warning("Error getting accessible entries for %s: %s", user_id, exc)
        return []


async def count_user_accessible_entries(user_id: str, track_id: str) -> int:
    """Count entries visible to the user in a track."""
    if not await can_view_track(user_id, track_id):
        return 0
    try:
        ctx_node = await Track.get(track_id)
        if not ctx_node:
            return 0
        ctx = await ctx_node.get_context()
        return await ctx.database.count(
            "node", {"entity": "Entry", "context.track_id": track_id}
        )
    except Exception:
        return len(await get_user_accessible_entries(user_id, track_id))
