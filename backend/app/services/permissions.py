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
  4. Direct ``COLLABORATES_ON`` edge contributes its complete role.
  5. ``EXCLUDED_FROM`` removes inherited candidates, preserving direct grants.
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
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from app.exceptions import AuthorizationUnavailableError
from app.middleware.permissions_cache import (
    isolated_permissions_cache,
    live_permissions_required,
    permissions_cache_get,
)
from app.models.edges import COLLABORATES_ON, EXCLUDED_FROM, OWNS
from app.models.nodes import App, Entry, Track, User, Workspace
from app.services.authority_facts import (
    primary_auth_record,
    primary_node_record,
    relationship_literal,
)
from app.services.perf_trace import perf_traced

logger = logging.getLogger(__name__)


def _permission_memo_enabled() -> bool:
    """Read current roles at live boundaries and during mutation-heavy tests."""
    return not (
        live_permissions_required()
        or os.getenv("TESTING")
        or os.getenv("PYTEST_CURRENT_TEST")
    )


async def get_user_node(user_id: str) -> Optional[User]:
    """Resolve current identity/profile facts without cached or ambiguous grants."""
    try:
        user = (
            None if user_id.startswith("o.") else await User.find_one({"id": user_id})
        )
        auth_id = str(user.user_id or "") if user is not None else user_id
        if user is not None and not auth_id.startswith("o.User."):
            # Existing graph-only actors are preserved here. Their explicit
            # trusted-service contract is separate from linked human identity.
            return user
        if not auth_id.startswith("o.User."):
            return None
        from jvspatial.core.context import get_default_context

        context = (
            await user.get_context() if user is not None else get_default_context()
        )
        identity = await primary_auth_record(context.database, auth_id)
        if identity is None or identity["context"]["is_active"] is not True:
            return None
        # Count raw candidates before hydration: an invalid duplicate profile
        # must not disappear and make the remaining profile look canonical.
        profiles = await context.database.find(
            "node",
            {
                "entity": User._entity_name(),
                "$or": [{"context.user_id": auth_id}, {"user_id": auth_id}],
            },
            limit=2,
        )
        if not profiles:
            return None
        if len(profiles) != 1:
            raise AuthorizationUnavailableError()
        row = profiles[0]
        if relationship_literal(row, "user_id", {auth_id}) != auth_id:
            raise AuthorizationUnavailableError()
        if user is not None:
            if row.get("id") != user.id:
                raise AuthorizationUnavailableError()
            return user
        profile = await context._deserialize_entity(User, row)
        if profile is None:
            raise AuthorizationUnavailableError()
        return profile
    except Exception as exc:
        logger.warning("Failed to resolve User for %s: %s", user_id, exc)
        raise AuthorizationUnavailableError() from exc


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


async def _governs(user_id: str, kind: str, resource_id: str, capability: str) -> bool:
    from app.services.workspace_governance import resolve_owner_governance

    return (
        await resolve_owner_governance(user_id, kind, resource_id, capability)
        is not None
    )


async def _is_workspace_owner_user(workspace: Workspace, user: User) -> bool:
    """True iff ``user`` holds the ``IS_MEMBER_OF{role:"owner"}`` edge.

    Uniform for both Personal and Organization — a Personal workspace
    always has exactly one such edge to its owning User.
    """
    from app.middleware.permissions_cache import isolated_permissions_cache
    from app.services.workspace_permissions import _current_workspace_role

    if live_permissions_required():
        return await _current_workspace_role(user, workspace) == "owner"
    with isolated_permissions_cache():
        current_user = await get_user_node(user.id)
        current_workspace = await Workspace.find_one({"id": workspace.id})
        if current_user is None or current_workspace is None:
            return False
        return await _current_workspace_role(current_user, current_workspace) == "owner"


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
    ws = await _load_resource_node("workspace", workspace_id)
    if not ws:
        return False
    if await _is_workspace_owner_user(ws, user):
        return True
    from app.services.workspace_permissions import member_creation_flags

    return (await member_creation_flags(user, workspace_id))[0]


async def can_create_track_under_workspace(user_id: str, workspace_id: str) -> bool:
    """Owner — or any member with the ``can_create_tracks`` grant — may create Tracks.

    Selective, explicitly-granted capability (see
    ``can_create_app_under_workspace``): the admin role does not auto-grant
    track creation; the ``IS_MEMBER_OF`` edge flag is authoritative.
    """
    user = await get_user_node(user_id)
    if not user:
        return False
    ws = await _load_resource_node("workspace", workspace_id)
    if not ws:
        return False
    if await _is_workspace_owner_user(ws, user):
        return True
    from app.services.workspace_permissions import member_creation_flags

    return (await member_creation_flags(user, workspace_id))[1]


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
    ws = await _load_resource_node("workspace", workspace_id)
    if not ws:
        return False
    if await _is_workspace_owner_user(ws, user):
        return True
    if ws.kind == "personal":
        # Single owner, no member pool — only the owner (handled above) publishes.
        return False
    from app.services.workspace_permissions import member_creation_flags

    return any(await member_creation_flags(user, workspace_id))


async def _user_in_workspace_member_pool(user: User, workspace_id: str) -> bool:
    """True if user has any IS_MEMBER_OF edge to the workspace (incl. guest)."""
    try:
        from app.services.workspace_permissions import read_member_facts
        from app.services.workspace_suspension import workspace_suspended

        if await workspace_suspended(user.id, workspace_id):
            return False
        return await read_member_facts(user, workspace_id) is not None
    except Exception as exc:
        logger.warning("workspace membership check failed: %s", exc)
        raise AuthorizationUnavailableError() from exc


# --- App-level ---


async def can_view_app(user_id: str, app_id: str) -> bool:
    """True if the user resolves to any role on the app_node.

    DEPRECATED — use ``policy_engine.evaluate(subject=Subject(kind='human', id=user_id),
    action='app.read', resource=Resource(kind='app', id=app_id, scope=f'app:{app_id}'))``.
    Retained as the engine's default-human delegation target; controller-layer
    callers must NOT invoke this directly (enforced by
    ``tests/test_no_legacy_helper_calls.py``).
    """
    app = await _load_resource_node("app", app_id)
    if not app or str(getattr(app, "lifecycle_state", "") or "") == "uninstalled":
        return False
    return await resolve_role(user_id, "app", app_id) is not None or await _governs(
        user_id, "app", app_id, "resource.read"
    )


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
        # Creation can memoize a denial before the ownership edge is wired.
        for key in list(cache):
            if (
                isinstance(key, tuple)
                and len(key) == 4
                and key[0] == "rr"
                and key[3] == user_id
            ):
                cache.pop(key, None)


async def get_user_accessible_apps(user_id: str) -> List[App]:
    """List current data access, including protected workspace stewardship."""
    with isolated_permissions_cache():
        return await _get_current_accessible_apps(user_id)


async def _get_current_accessible_apps(user_id: str) -> List[App]:
    """Apps via ownership, collaboration, staff inventory, and visibility grants.

    Org workspace members additionally see apps with ``visibility`` workspace
    or public (ARCHITECTURE §9.5). Guests require explicit ``COLLABORATES_ON``.
    Direct grants are pruned against live workspace membership before listing.
    """
    cache = permissions_cache_get() if _permission_memo_enabled() else None
    ck = ("accessible_apps", user_id)
    if cache is not None and ck in cache:
        return cache[ck]

    from app.services import permissions_process_cache as _ppc

    cache_generation = _ppc.generation(user_id)
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
            if sp.id not in seen:
                seen.add(sp.id)
                result.append(sp)

        from app.services.workspace_permissions import (
            _list_current_accessible_workspaces,
            collect_org_workspace_member_visibility_inventory,
            collect_org_workspace_staff_inventory,
        )

        workspace_roles: dict[str, str] = {}
        workspaces = await _list_current_accessible_workspaces(
            user, roles_out=workspace_roles
        )
        accessible_workspace_ids = {ws.id for ws in workspaces}

        direct_apps: List[App] = []
        direct_apps.extend(await user.nodes(edge=["OWNS"], node=["WorkspaceApp"]))
        direct_apps.extend(
            await user.nodes(edge=["COLLABORATES_ON"], node=["WorkspaceApp"])
        )
        outside_apps: List[App] = []
        for sp in direct_apps:
            workspace_id = str(getattr(sp, "workspace_id", "") or "")
            if workspace_id in accessible_workspace_ids:
                _add(sp)
            elif workspace_id:
                outside_apps.append(sp)
        if outside_apps:
            # A public App can remain readable after membership revocation,
            # but its stale direct edge cannot restore write authority.
            roles = await asyncio.gather(
                *(resolve_role(user_id, "app", sp.id) for sp in outside_apps)
            )
            for sp, role in zip(outside_apps, roles):
                if role is not None:
                    _add(sp)

        # Org workspace owner/admin: full catalogued inventory without
        # per-app COLLABORATES_ON edges (members still require explicit grants).
        for ws in workspaces:
            ws_role = workspace_roles[ws.id]
            if ws_role in ("owner", "admin"):
                staff_apps, _ = await collect_org_workspace_staff_inventory(ws)
                for sp in staff_apps:
                    if (
                        ws_role != "owner"
                        and await effective_resource_visibility("app", sp) == "private"
                    ):
                        continue
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

        result = await _filter_canonical_resource_inventory(
            user_id, "app", result, principal=user, admitted_workspaces=workspace_roles
        )
        if cache is not None:
            cache[ck] = result
        _ppc.set_cached(
            user_id,
            "accessible_apps",
            result,
            expected_generation=cache_generation,
        )
        return result
    except Exception as exc:
        logger.warning("Error getting accessible apps for %s: %s", user_id, exc)
        raise AuthorizationUnavailableError() from exc


async def can_edit_app(user_id: str, app_id: str) -> bool:
    """True if the user is an owner, admin, or editor on the app_node.

    "editor" = entry CRUD authority (within Tracks under this App).
    "admin"  = entry CRUD + App-config authority (schema/views/tags
               cascade via ``DEFINES_TRACK_PROFILE``).

    DEPRECATED — use ``policy_engine.evaluate(action='app.update', ...)``.
    See ``can_view_app`` docstring for the migration contract.
    """
    return await resolve_role(user_id, "app", app_id) in (
        "owner",
        "admin",
        "editor",
    ) or await _governs(user_id, "app", app_id, "resource.write")


async def _direct_management_role(
    user_id: str, kind: str, resource_id: str
) -> str | None:
    """Retain direct stewardship of non-executing installations for recovery.

    Inherited roles cannot acquire config authority from this path. Current
    identity, canonical containment and workspace membership remain mandatory.
    """
    from app.services.resource_location import MANAGEMENT_APP_STATES, locate_resource

    with isolated_permissions_cache():
        user = await get_user_node(user_id)
        if user is None:
            return None
        location = await locate_resource(
            kind, resource_id, allowed_app_states=MANAGEMENT_APP_STATES
        )
        if location is None or not await _user_in_workspace_member_pool(
            user, location.workspace_id
        ):
            return None
        from app.services.resource_access_blocks import resource_blocked
        from app.services.share_grant_sources import share_grant_roles

        if await resource_blocked(user.id, kind, resource_id, location=location):
            return None
        direct = await _direct_role_on_resource(user, resource_id)
        sourced = await share_grant_roles(user.id, kind, resource_id)
        return max(
            ([direct] if direct else []) + sourced,
            key=lambda value: ROLE_RANK[value],
            default=None,
        )


async def can_admin_app(user_id: str, app_id: str) -> bool:
    """True if the user is owner or admin on the App.

    Gates App-config authority: attached OperationalModel mutations,
    template-track edits, library-derivation. NOT editor-accessible —
    editors get entry CRUD only.
    """
    return (
        await resolve_role(user_id, "app", app_id)
        in (
            "owner",
            "admin",
        )
        or await _direct_management_role(user_id, "app", app_id) in ("owner", "admin")
        or await _governs(user_id, "app", app_id, "resource.configure")
    )


async def can_delete_app(user_id: str, app_id: str) -> bool:
    """True if the user owns the app_node.

    DEPRECATED — use ``policy_engine.evaluate(action='app.delete', ...)``.
    See ``can_view_app`` docstring for the migration contract.
    """
    return (
        await resolve_role(user_id, "app", app_id) == "owner"
        or await _direct_management_role(user_id, "app", app_id) == "owner"
        or await _governs(user_id, "app", app_id, "resource.delete")
    )


# --- Track-level ---


async def can_view_track(user_id: str, track_id: str) -> bool:
    """True if the user resolves to any effective track role.

    DEPRECATED — use ``policy_engine.evaluate(action='track.read', ...)``. See
    ``can_view_app`` docstring for the migration contract.
    """
    return await resolve_role(user_id, "track", track_id) is not None or await _governs(
        user_id, "track", track_id, "resource.read"
    )


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
    ) or await _governs(user_id, "track", track_id, "resource.write")


async def can_admin_track(user_id: str, track_id: str) -> bool:
    """Current Track curators and its containing App's owner may configure it.

    Gates track-config authority (schema, views, tags, library, anchors,
    migrations, default view, detach/revert). Editors do NOT pass — they
    get entry CRUD only.
    """
    return (
        await resolve_role(user_id, "track", track_id)
        in (
            "owner",
            "admin",
        )
        or await _direct_management_role(user_id, "track", track_id)
        in ("owner", "admin")
        or await _app_owner_can_configure_track(user_id, track_id)
        or await _governs(user_id, "track", track_id, "resource.configure")
    )


async def _app_owner_can_configure_track(user_id: str, track_id: str) -> bool:
    """App ownership covers its own structure, never sharing or child deletion.

    Keep the ordinary inherited-role cap. This explicit configuration rule
    proves current containment and ownership rather than granting an inherited
    admin role, and does not extend to App admins or other collaborators.
    """
    from app.services.resource_location import MANAGEMENT_APP_STATES, locate_resource

    with isolated_permissions_cache():
        location = await locate_resource(
            "track", track_id, allowed_app_states=MANAGEMENT_APP_STATES
        )
        if location is None or not location.app_id:
            return False
        if await _direct_management_role(user_id, "app", location.app_id) != "owner":
            return False
        user = await get_user_node(user_id)
        if user is None or await _is_excluded_from_resource(user, track_id):
            return False
        from app.services.resource_access_blocks import resource_blocked

        return not await resource_blocked(user.id, "track", track_id, location=location)


async def can_moderate_track(user_id: str, track_id: str) -> bool:
    """Comment moderation retains exact-resource curation authority."""
    return (
        await resolve_role(user_id, "track", track_id) in ("owner", "admin")
        or await _direct_management_role(user_id, "track", track_id)
        in ("owner", "admin")
        or await _governs(user_id, "track", track_id, "resource.configure")
    )


async def can_delete_track(user_id: str, track_id: str) -> bool:
    """True if the user owns the track.

    DEPRECATED — use ``policy_engine.evaluate(action='track.delete', ...)``.
    See ``can_view_app`` docstring for the migration contract.
    """
    return (
        await resolve_role(user_id, "track", track_id) == "owner"
        or await _direct_management_role(user_id, "track", track_id) == "owner"
        or await _governs(user_id, "track", track_id, "resource.delete")
    )


# --- Entry-level ---


async def can_view_entry(user_id: str, entry_id: str) -> bool:
    """True if the user may read the entry.

    Resolves through the parent Track, honouring per-entry visibility opt-out
    and per-user exclusion. Entries follow track access only — no author
    shortcut at view time (rule 5 + AGENTS.md "no per-entry ACL" baseline).

    DEPRECATED — use ``policy_engine.evaluate(action='entry.read', ...)``. See
    ``can_view_app`` docstring for the migration contract.
    """
    return await resolve_role(user_id, "entry", entry_id) is not None or await _governs(
        user_id, "entry", entry_id, "resource.read"
    )


async def can_edit_entry(user_id: str, entry_id: str) -> bool:
    """True if the user is the author or can edit via cascade / direct grant.

    Roles owner / admin / editor all carry entry-CRUD authority on the
    parent track and therefore beat the per-entry role check. Only a
    commenter can use the author shortcut (own entries only).

    DEPRECATED — use ``policy_engine.evaluate(action='entry.update', ...)``.
    See ``can_view_app`` docstring for the migration contract.
    """
    role = await resolve_role(user_id, "entry", entry_id)
    if role in ("owner", "admin", "editor"):
        return True
    if await _governs(user_id, "entry", entry_id, "resource.write"):
        return True
    # EXCLUDED_FROM / no grant → resolve_role is None; do not let author shortcut
    # resurrect edit after an explicit deny.
    if role != "commenter":
        return False
    try:
        entry = await _load_resource_node("entry", entry_id)
        user = await get_user_node(user_id)
        identities = {user_id, user.id, user.user_id} if user is not None else set()
        return bool(entry and entry.author_id and entry.author_id in identities)
    except Exception as exc:
        logger.warning("Error checking entry edit permission for %s: %s", user_id, exc)
        raise AuthorizationUnavailableError() from exc


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
    from app.services.view_scope import read_scoped_view

    with isolated_permissions_cache():
        located = await read_scoped_view(view_id)
        return located is not None and await can_view_track(
            user_id, located[1].track_id
        )


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
    from app.services.resource_location import MANAGEMENT_APP_STATES
    from app.services.view_scope import read_scoped_view

    with isolated_permissions_cache():
        located = await read_scoped_view(
            view_id, allowed_app_states=MANAGEMENT_APP_STATES
        )
        return located is not None and await can_admin_track(
            user_id, located[1].track_id
        )


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
    if resource_type not in {"app", "track"}:
        return "private"
    from app.services.resource_location import DATA_APP_STATES, locate_resource

    context = await node.get_context()
    model = App if resource_type == "app" else Track
    row = await primary_node_record(context.database, model, node.id)
    if row is None:
        raise AuthorizationUnavailableError()
    allowed = {"private", "workspace", "organization", "public"}
    if resource_type == "track":
        allowed.add("inherit")
    vis = relationship_literal(row, "visibility", allowed)
    if vis != "inherit":
        return _VISIBILITY_ALIASES.get(vis, vis)
    location = await locate_resource(
        "track", node.id, allowed_app_states=DATA_APP_STATES
    )
    if location is None:
        raise AuthorizationUnavailableError()
    if not location.app_id:
        return "private"
    parent = await App.find_one({"id": location.app_id})
    if parent is None:
        raise AuthorizationUnavailableError()
    return await effective_resource_visibility("app", parent)


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
        track = await _load_resource_node("track", track_id)
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
        ws = await _load_resource_node("workspace", ws_id)
        if not ws or getattr(ws, "kind", "") != "organization":
            return None
        from app.services.workspace_permissions import user_in_workspace_cascade_view

        if not await user_in_workspace_cascade_view(user_id, ws_id):
            return None
        return "viewer"
    return None


def _cap_inherited_role(role: str) -> str:
    """Cap ordinary inherited roles; App-owner configuration is a separate gate.

    See I-ROLE-02. Sharing, ownership and deletion remain exact-resource
    decisions; the configuration exception never produces an admin role.
    """
    return "editor" if role in ("owner", "admin") else role


async def _none() -> None:
    """Awaitable ``None`` — placeholder in an ``asyncio.gather`` whose other
    branch is conditional, so the gather shape stays fixed."""
    return None


def _direct_role_from_rows(
    rows: list[dict], *, creator_bindings_validated: bool = False
) -> Optional[str]:
    """Validate the complete raw grant set before selecting its strongest role."""
    owns = [row for row in rows if row.get("entity") == OWNS.__name__]
    collaborators = [
        row for row in rows if row.get("entity") == COLLABORATES_ON.__name__
    ]
    if len(owns) > 1 or len(collaborators) > 1:
        raise AuthorizationUnavailableError()
    from app.services.model_creator_grants import creator_grant_binding

    for row in collaborators:
        if creator_grant_binding(row) and not creator_bindings_validated:
            # Bulk App/Track/Entry resolvers must not treat Model-only metadata
            # as an ordinary role without validating its current lifetime.
            raise AuthorizationUnavailableError()
    role = (
        relationship_literal(collaborators[0], "role", ROLE_RANK)
        if collaborators
        else None
    )
    if owns:
        relationship_literal(owns[0], "role", {"owner"})
        # Optional audit metadata cannot hide this complete direct path.
        return "owner"
    if role is None:
        return None
    return role


async def _direct_role_on_resource(user: User, resource_id: str) -> Optional[str]:
    """Direct OWNS or COLLABORATES_ON role on this exact resource, or None."""
    try:
        ctx = await user.get_context()
        rows = await ctx.database.find(
            "edge",
            {
                "source": user.id,
                "target": resource_id,
                "entity": {"$in": [OWNS.__name__, COLLABORATES_ON.__name__]},
            },
            limit=3,
        )
        if any(
            row.get("source") != user.id
            or row.get("target") != resource_id
            or row.get("entity") not in {OWNS.__name__, COLLABORATES_ON.__name__}
            for row in rows
        ):
            raise AuthorizationUnavailableError()
        from app.services.model_creator_grants import current_creator_grant

        # Validate cardinality and role vocabulary before removing a stale
        # lifetime: corruption must not disappear into a valid weaker path.
        _direct_role_from_rows(rows, creator_bindings_validated=True)
        current = []
        for row in rows:
            if row.get(
                "entity"
            ) != COLLABORATES_ON.__name__ or await current_creator_grant(
                user, resource_id, row
            ):
                current.append(row)
        return _direct_role_from_rows(current, creator_bindings_validated=True)
    except Exception as exc:
        logger.warning(
            "Error reading direct role on %s for %s: %s", resource_id, user.id, exc
        )
        raise AuthorizationUnavailableError() from exc


async def _is_excluded_from_resource(user: User, resource_id: str) -> bool:
    """True iff an EXCLUDED_FROM edge exists from this user to this resource."""
    try:
        # Optional audit metadata must not determine whether a deny exists.
        # Edge hydration skips malformed metadata; count reads presence itself
        # on the current graph/transaction context, without deserialization.
        return await EXCLUDED_FROM.count({"source": user.id, "target": resource_id}) > 0
    except Exception as exc:
        logger.warning(
            "Error reading exclusion on %s for %s: %s", resource_id, user.id, exc
        )
        raise AuthorizationUnavailableError() from exc


async def _parent_of_resource(resource_type: str, resource_id: str) -> Optional[tuple]:
    """Return (parent_type, parent_id) or None if the resource has no parent."""
    try:
        from app.services.resource_location import DATA_APP_STATES, locate_resource

        location = await locate_resource(
            resource_type, resource_id, allowed_app_states=DATA_APP_STATES
        )
        if location is None:
            return None
        if resource_type == "entry":
            return ("track", location.track_id)
        if resource_type == "track" and location.app_id:
            return ("app", location.app_id)
        # App and Workspace have no inherited-access parent (workspace
        # membership does not cascade).
        return None
    except Exception as exc:
        logger.warning(
            "Error finding parent of %s %s: %s", resource_type, resource_id, exc
        )
        raise AuthorizationUnavailableError() from exc


async def _load_resource_node(resource_type: str, resource_id: str) -> Any:
    if live_permissions_required():
        model = {
            "workspace": Workspace,
            "app": App,
            "track": Track,
            "entry": Entry,
        }.get(resource_type)
        if model is None:
            return None
        return await model.find_one({"id": resource_id})
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
    """Require the same complete canonical anchor used by point authorization."""
    try:
        from app.services.resource_location import DATA_APP_STATES, locate_resource

        location = await locate_resource(
            resource_type, node.id, allowed_app_states=DATA_APP_STATES
        )
        return location.workspace_id if location is not None else None
    except Exception as exc:
        logger.warning("workspace anchor lookup failed for %s: %s", resource_type, exc)
        raise AuthorizationUnavailableError() from exc
    return None


async def resolve_role(
    user_id: str,
    resource_type: str,
    resource_id: str,
) -> Optional[str]:
    """Resolve ordinary roles from current facts, never protected governance."""
    # All callers, including workers outside the HTTP middleware, need current
    # facts. Epoch-keyed memoization requires adoption by every ACL writer;
    # a TTL or process-local generation cannot establish that guarantee.
    with isolated_permissions_cache():
        return await _resolve_current_role(user_id, resource_type, resource_id)


async def _resolve_current_role(
    user_id: str, resource_type: str, resource_id: str
) -> Optional[str]:
    """Return the user's effective role on a resource, or None.

    Resolution order (highest wins; deny blocks inheritance only):

      1. Personal-workspace owner short-circuit (workspace only).
      2. Workspace membership gate for app/track/entry resources.
      3. Direct OWNS edge on the resource → "owner".
      4. Direct COLLABORATES_ON edge → direct role candidate.
      5. EXCLUDED_FROM removes inherited candidates, preserving direct grants.
      6. Among complete direct and inherited candidates, strongest wins (ROLE_RANK):
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

    cache_generation = _ppc.generation(user_id)
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
            _ppc.set_resolve_role_cached(
                user_id,
                resource_type,
                resource_id,
                role,
                expected_generation=cache_generation,
            )
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
        from app.services.resource_location import locate_resource
        from app.services.workspace_permissions import can_access_workspace

        if await locate_resource("workspace", resource_id) is None:
            return _finish(None)
        role = await can_access_workspace(user.id, resource_id)
        return _finish(
            {
                "owner": "owner",
                "admin": "editor",
                "member": "editor",
                "guest": "viewer",
            }.get(role)
        )

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
    from app.services.resource_location import DATA_APP_STATES, locate_resource

    direct, location = await asyncio.gather(
        _direct_role_on_resource(user, resource_id),
        locate_resource(resource_type, resource_id, allowed_app_states=DATA_APP_STATES),
    )
    if location is None:
        return _finish(None)
    workspace_id = location.workspace_id
    from app.services.workspace_suspension import workspace_suspended

    if await workspace_suspended(user.id, workspace_id):
        return _finish(None)
    from app.services.resource_access_blocks import resource_blocked

    if await resource_blocked(user.id, resource_type, resource_id, location=location):
        return _finish(None)
    from app.services.share_grant_sources import share_grant_roles

    sourced = await share_grant_roles(user.id, resource_type, resource_id)
    direct = max(
        ([direct] if direct else []) + sourced,
        key=lambda value: ROLE_RANK[value],
        default=None,
    )
    if not await _user_in_workspace_member_pool(user, workspace_id):
        ws = await _load_resource_node("workspace", workspace_id)
        # A resource OWNS edge survives a workspace-membership revoke. It
        # must not bypass the workspace gate: former members may retain that
        # edge on resources they created, but no longer have workspace access.
        workspace_allowed = ws is not None and await _is_workspace_owner_user(ws, user)
        if not workspace_allowed:
            if not await _is_publicly_readable_resource(resource_type, node):
                return _finish(None)
            # Public visibility grants read only. A stale direct ownership or
            # collaborator edge cannot restore write authority after revoke.
            if await _is_excluded_from_resource(user, resource_id):
                return _finish(None)
            return _finish("viewer")

    # Direct owner is already the maximum role. Exclusions only remove
    # inherited grants, and inherited roles cap at editor. After the workspace
    # gate above, no additional candidate can change this complete grant.
    if direct == "owner":
        return _finish(direct)

    if await _is_excluded_from_resource(user, resource_id):
        # Explicit exclusions remove inherited candidates, not direct grants.
        return _finish(direct)

    # Candidates from staff / visibility / parent cascade. Visibility is a
    # read floor (viewer), not a ceiling — an App editor on a workspace-
    # visible track must still resolve as editor so comment/edit gates work
    # (ARCHITECTURE §9 "strongest wins"; QA: full-permission users blocked
    # from commenting when visibility short-circuited before cascade).
    candidates: list[str] = [direct] if direct is not None else []

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
            # Private means the resource owner and people they add. Staff
            # inventory does not open it. A direct OWNS / COLLABORATES_ON
            # grant already returned above.
            if await effective_resource_visibility(resource_type, node) != "private":
                candidates.append(implicit)

        if vis_grant:
            candidates.append(vis_grant)

    parent = None
    if resource_type == "entry":
        parent = ("track", location.track_id)
    elif resource_type == "track" and location.app_id:
        parent = ("app", location.app_id)
    if parent is not None:
        skip_cascade = (
            resource_type == "track"
            and await effective_resource_visibility("track", node) == "private"
            and await _direct_role_on_resource(user, parent[1]) is None
        )
        # Private tracks hide from workspace-wide visibility grants on the
        # parent App; App collaborators (direct OWNS / COLLABORATES_ON) still
        # inherit entry access. Org staff do not reach private inventory
        # through the implicit staff role.
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
    """List current data access, including protected workspace stewardship."""
    with isolated_permissions_cache():
        return await _get_current_accessible_tracks(user_id)


async def _get_current_accessible_tracks(user_id: str) -> List[Track]:
    """Tracks via ownership, collaboration, app-cascade, staff inventory, visibility.

    Direct grants require live workspace membership or a public-read role;
    cascade and visibility candidates pass through ``resolve_role``.
    """
    cache = permissions_cache_get() if _permission_memo_enabled() else None
    ck = ("accessible_tracks", user_id)
    if cache is not None and ck in cache:
        return cache[ck]

    from app.services import permissions_process_cache as _ppc

    cache_generation = _ppc.generation(user_id)
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
        # Candidate discovery only: share in-flight parent checks within this
        # inventory. The final fresh canonical admission below still decides
        # access. Do not retain these tasks across calls or reuse them as grants.
        # Measured dev inventory: 79 Tracks repeatedly resolve the same 10 Apps.
        listability_tasks: dict[str, asyncio.Task] = {}
        app_visibility_tasks: dict[str, asyncio.Task] = {}

        def _add(track: Track) -> None:
            if track.id not in seen:
                seen.add(track.id)
                result.append(track)

        async def _track_listable(track: Track) -> bool:
            task = listability_tasks.get(track.id)
            if task is None:
                task = asyncio.create_task(_check_track_listable(track))
                listability_tasks[track.id] = task
            return await task

        async def _check_track_listable(track: Track) -> bool:
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
                task = app_visibility_tasks.get(app_node.id)
                if task is None:
                    task = asyncio.create_task(can_view_app(user_id, app_node.id))
                    app_visibility_tasks[app_node.id] = task
                if await task:
                    return True
            return False

        from app.services.workspace_permissions import (
            _list_current_accessible_workspaces,
            collect_org_workspace_member_visibility_inventory,
            collect_org_workspace_staff_inventory,
        )

        workspace_roles: dict[str, str] = {}
        workspaces = await _list_current_accessible_workspaces(
            user, roles_out=workspace_roles
        )
        accessible_workspace_ids = {ws.id for ws in workspaces}

        # deviation: Reuse the workspace inventory for direct-grant checks —
        # measured 65 vs 55 DB round-trips for a 3-track list when calling
        # resolve_role on every direct Track. Direct edges beat exclusions;
        # only out-of-pool candidates need the full public-read resolution.
        cascade_apps, direct_tracks = await _direct_inventory_candidates(user)
        if direct_tracks:
            listable = await asyncio.gather(
                *(_track_listable(t) for t in direct_tracks)
            )
            outside_tracks: List[Track] = []
            for t, ok in zip(direct_tracks, listable):
                if not ok:
                    continue
                workspace_id = str(getattr(t, "workspace_id", "") or "")
                if workspace_id in accessible_workspace_ids:
                    _add(t)
                elif workspace_id:
                    outside_tracks.append(t)
            if outside_tracks:
                roles = await asyncio.gather(
                    *(resolve_role(user_id, "track", t.id) for t in outside_tracks)
                )
                for t, role in zip(outside_tracks, roles):
                    if role is not None:
                        _add(t)

        # App-cascade — must clear the per-track visibility / exclusion gate, so
        # route through resolve_role rather than blindly including. Capture each
        # candidate's parent app so we can warm the app-level role cache once,
        # then resolve every track role concurrently — each track recurses to
        # its parent app, which is now a cache hit rather than a fresh walk.
        candidate_tracks: dict = {}
        candidate_app_of: dict = {}
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

        for ws in workspaces:
            ws_role = workspace_roles[ws.id]
            if ws_role in ("owner", "admin"):
                _, staff_tracks = await collect_org_workspace_staff_inventory(ws)
                if staff_tracks:
                    listable = await asyncio.gather(
                        *(_track_listable(t) for t in staff_tracks)
                    )
                    for t, ok in zip(staff_tracks, listable):
                        if not ok:
                            continue
                        if (
                            ws_role != "owner"
                            and await effective_resource_visibility("track", t)
                            == "private"
                        ):
                            continue
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

        result = await _filter_canonical_resource_inventory(
            user_id,
            "track",
            result,
            principal=user,
            admitted_workspaces=workspace_roles,
        )
        if cache is not None:
            cache[ck] = result
        _ppc.set_cached(
            user_id,
            "accessible_tracks",
            result,
            expected_generation=cache_generation,
        )
        return result
    except Exception as exc:
        logger.warning("Error getting accessible tracks for %s: %s", user_id, exc)
        raise AuthorizationUnavailableError() from exc


async def _direct_inventory_candidates(user: User) -> tuple[list[App], list[Track]]:
    """Read complete raw direct-grant pages, then hydrate only candidate resources."""
    context = await user.get_context()
    cursor = ""
    apps: dict[str, App] = {}
    tracks: dict[str, Track] = {}
    while True:
        query: dict[str, Any] = {
            "source": user.id,
            "entity": {"$in": [OWNS.__name__, COLLABORATES_ON.__name__]},
        }
        if cursor:
            query["id"] = {"$gt": cursor}
        rows = await context.database.find("edge", query, limit=501, sort=[("id", 1)])
        page = rows[:500]
        if not page:
            break
        targets = set()
        for row in page:
            if (
                row.get("source") != user.id
                or not isinstance(row.get("target"), str)
                or not row["target"]
                or not isinstance(row.get("id"), str)
                or row["id"] <= cursor
            ):
                raise AuthorizationUnavailableError()
            entity = row.get("entity")
            if entity == OWNS.__name__:
                relationship_literal(row, "role", {"owner"})
            elif entity == COLLABORATES_ON.__name__:
                relationship_literal(row, "role", ROLE_RANK)
            else:
                raise AuthorizationUnavailableError()
            targets.add(row["target"])
        records = await context.database.find(
            "node", {"id": {"$in": sorted(targets)}}, limit=len(targets) + 1
        )
        node_ids = [row.get("id") for row in records]
        if len(node_ids) != len(set(node_ids)) or not set(node_ids).issubset(targets):
            raise AuthorizationUnavailableError()
        for row in records:
            entity = row.get("entity")
            model = (
                App
                if entity == App._entity_name()
                else Track if entity == Track._entity_name() else None
            )
            if model is None:
                continue
            node = await context._deserialize_entity(model, row)
            if node is None:
                raise AuthorizationUnavailableError()
            if model is App:
                apps[node.id] = node
            else:
                tracks[node.id] = node
        if len(rows) <= 500:
            break
        cursor = page[-1]["id"]
    from app.services.share_grant_sources import share_grant_targets

    for kind, model, bucket in (("app", App, apps), ("track", Track, tracks)):
        for identity in await share_grant_targets(user.id, kind):
            record = await context.database.find(
                "node", {"id": identity, "entity": model._entity_name()}, limit=2
            )
            if len(record) != 1:
                raise AuthorizationUnavailableError()
            node = await context._deserialize_entity(model, record[0])
            if node is None:
                raise AuthorizationUnavailableError()
            bucket[node.id] = node
    return list(apps.values()), list(tracks.values())


async def _filter_canonical_resource_inventory(
    user_id: str,
    kind: str,
    candidates: list[Any],
    *,
    principal: User | None = None,
    admitted_workspaces: dict[str, str] | None = None,
) -> list[Any]:
    """Batch fresh containment and direct facts; resolve remaining ordinary paths."""
    if not candidates:
        return []
    from app.services.resource_location import DATA_APP_STATES, locate_resources
    from app.services.workspace_governance import owner_governance_allowed
    from app.services.workspace_permissions import _current_workspace_role

    user = principal if principal is not None else await get_user_node(user_id)
    if user is None:
        return []
    result = []
    for offset in range(0, len(candidates), 500):
        page = candidates[offset : offset + 500]
        ids = list(dict.fromkeys(node.id for node in page))
        locations = await locate_resources(
            kind,
            ids,
            allowed_app_states=DATA_APP_STATES,
            workspace_hints=[
                node.workspace_id for node in page if getattr(node, "workspace_id", "")
            ],
        )
        context = await user.get_context()
        rows = await context.database.find(
            "edge",
            {
                "source": user.id,
                "target": {"$in": ids},
                "entity": {"$in": [OWNS.__name__, COLLABORATES_ON.__name__]},
            },
            limit=2 * len(ids) + 1,
        )
        if len(rows) > 2 * len(ids):
            raise AuthorizationUnavailableError()
        grants: dict[str, list[dict]] = {node_id: [] for node_id in ids}
        for row in rows:
            if (
                row.get("source") != user.id
                or row.get("target") not in grants
                or row.get("entity") not in (OWNS.__name__, COLLABORATES_ON.__name__)
            ):
                raise AuthorizationUnavailableError()
            grants[row["target"]].append(row)
        direct = {
            node_id: _direct_role_from_rows(group) for node_id, group in grants.items()
        }
        workspace_roles = dict(admitted_workspaces or {})
        for location in locations.values():
            if location is None or location.workspace_id in workspace_roles:
                continue
            workspace = await _load_resource_node("workspace", location.workspace_id)
            workspace_roles[location.workspace_id] = (
                await _current_workspace_role(user, workspace)
                if workspace is not None
                else "none"
            )
        for node in page:
            location = locations[node.id]
            if location is None:
                continue
            role = workspace_roles.get(location.workspace_id, "none")
            from app.services.resource_access_blocks import resource_blocked

            if not (
                role == "owner" and owner_governance_allowed()
            ) and await resource_blocked(user.id, kind, node.id, location=location):
                continue
            if (
                (role == "owner" and owner_governance_allowed())
                or (role != "none" and direct[node.id] is not None)
                or await resolve_role(user_id, kind, node.id) is not None
            ):
                result.append(node)
    return result


async def _collect_entries_for_tracks(
    user_id: str,
    track_ids: List[str],
    *,
    include_author_entries: bool = True,
    candidate_query: Optional[Dict[str, Any]] = None,
) -> List[Entry]:
    """Discover requested candidates and admit every Entry before exposure."""
    from app.services.entry_access_projection import (
        hydrate_records,
        iter_records,
        merge_queries,
    )

    sources = [{"context.track_id": {"$in": track_ids}}] if track_ids else []
    if include_author_entries:
        user = await get_user_node(user_id)
        if user is None:
            return []
        sources.append({"context.author_id": {"$in": [user.id, user.user_id]}})
    if not sources:
        return []
    query = merge_queries({"$or": sources}, candidate_query or {})
    result = []
    async for records in iter_records(user_id, query):
        result.extend(await hydrate_records(records))
    return result


@perf_traced("permissions.get_user_accessible_entries")
async def get_user_accessible_entries(
    user_id: str,
    track_id: Optional[str] = None,
    app_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    *,
    strict: bool = False,
    candidate_query: Optional[Dict[str, Any]] = None,
) -> List[Entry]:
    """List the current canonical Entry audience in bounded raw windows.

    ``strict`` remains a source-compatible argument. All callers propagate
    unavailable authority; a failed read never becomes an empty success.
    """
    from app.services.entry_access_projection import candidate_query as discover
    from app.services.entry_access_projection import (
        hydrate_records,
        iter_records,
        merge_queries,
    )

    scope = {
        "track_id": track_id,
        "app_id": None if track_id else app_id,
        "workspace_id": workspace_id,
    }
    query = await discover(user_id, **scope)
    if query is None:
        return []
    result = []
    async for records in iter_records(
        user_id, merge_queries(query, candidate_query or {}), **scope
    ):
        result.extend(await hydrate_records(records))
    return result


async def count_user_accessible_entries_for_tracks(
    user_id: str, tracks: list[Track]
) -> dict[str, int]:
    """Count a page's Entries in shared bounded admission windows.

    Each row still passes the canonical audience projection. Scanning once
    avoids repeating candidate discovery and parent-graph admission per Track.
    """
    from app.services.entry_access_projection import iter_records

    result = {track.id: 0 for track in tracks}
    admitted = await _filter_canonical_resource_inventory(user_id, "track", tracks)
    if not admitted:
        return result
    ids = [track.id for track in admitted]
    async for records in iter_records(
        user_id, {"context.track_id": {"$in": ids}}, projection=[], batch_size=2500
    ):
        for record in records:
            track_id = record["context"]["track_id"]
            if track_id in result:
                result[track_id] += 1
    return result


async def count_user_accessible_entries(user_id: str, track_id: str) -> int:
    """Count only Entries admitted by current canonical authority."""
    from app.services.entry_access_projection import (
        candidate_query,
        count_bound_records,
        iter_records,
    )

    query = await candidate_query(user_id, track_id=track_id)
    if query is None:
        return 0
    exact = await count_bound_records(user_id, track_id, query)
    if exact is not None:
        return exact
    total = 0
    async for records in iter_records(
        user_id, query, track_id=track_id, projection=[], batch_size=2500
    ):
        total += len(records)
    return total


async def get_user_accessible_entry_page(
    user_id: str,
    track_id: str,
    *,
    limit: int,
    offset: int = 0,
    sort: Optional[List[Tuple[str, int]]] = None,
    candidate_query: Optional[Dict[str, Any]] = None,
    workspace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Return an admitted offset page and its exact current audience total."""
    from app.services.entry_access_projection import candidate_query as discover
    from app.services.entry_access_projection import merge_queries, select_page

    if limit < 1 or offset < 0:
        return {"entries": [], "total": 0, "next_cursor": None}
    scope = {"track_id": track_id, "workspace_id": workspace_id}
    query = await discover(user_id, **scope)
    if query is None:
        return {"entries": [], "total": 0, "next_cursor": None}
    entries, response = await select_page(
        user_id,
        merge_queries(query, candidate_query or {}),
        limit=limit,
        offset=offset,
        sort=sort,
        include_total=True,
        **scope,
    )
    return {
        "entries": entries,
        "total": response["total"],
        "next_cursor": response["next_cursor"],
    }


async def iter_user_accessible_entry_batches(
    user_id: str,
    track_id: str,
    *,
    batch_size: int = 5000,
    candidate_query: Optional[Dict[str, Any]] = None,
    workspace_id: Optional[str] = None,
) -> AsyncIterator[List[Entry]]:
    """Stream admitted Entries while bounding every raw database window."""
    from app.services.entry_access_projection import candidate_query as discover
    from app.services.entry_access_projection import (
        hydrate_records,
        iter_records,
        merge_queries,
    )

    if batch_size < 1:
        return
    scope = {"track_id": track_id, "workspace_id": workspace_id}
    query = await discover(user_id, **scope)
    if query is None:
        return
    async for records in iter_records(
        user_id,
        merge_queries(query, candidate_query or {}),
        batch_size=batch_size,
        **scope,
    ):
        yield await hydrate_records(records)
