"""Integral application graph shell: Root→IntegralApp→registries and catalog helpers.

Phase 10 / Plan 10-01: the singleton class was renamed ``App`` → ``IntegralApp``
to free the Python class name ``App`` for the user-facing primitive (formerly
``App``). The persisted ``__entity_name__`` discriminator stays
``"IntegralApp"`` — no DB schema effect on this class.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Type, cast

from jvspatial.core import Edge, Node
from jvspatial.core.entities import Root

from app.models.edges import (
    CATALOGS,
    COLLABORATES_ON,
    CONTAINS,
    HAS_CONTENT_PROFILE,
    OWNS,
)
from app.models.nodes import (
    APP_NODE_ID,
    CONTENT_PROFILES_REGISTRY_ID,
    INVITATIONS_REGISTRY_ID,
    USERS_REGISTRY_ID,
    WORKSPACES_REGISTRY_ID,
    App,
    Apps,
    ChatThread,
    ChatThreads,
    ContentProfile,
    ContentProfiles,
    Dashboards,
    EntryType,
    IntegralApp,
    Invitation,
    Invitations,
    Notification,
    Track,
    Tracks,
    User,
    Users,
    View,
    Views,
    Workspace,
    Workspaces,
)
from app.services.content_profile_library_sync import sync_library_catalog_from_disk

logger = logging.getLogger(__name__)


async def _ensure_fixed_node(cls: Type[Node], node_id: str, **defaults: Any) -> Node:
    existing = await cls.get(node_id)
    if existing:
        return existing
    node = cls(id=node_id, **defaults)
    await node.save()
    return node


async def ensure_catalog_edge(
    registry: Node, instance: Node, *, cataloged_at: Optional[str] = None
) -> None:
    """Add CATALOGS edge from registry to instance if missing."""
    ctx = await registry.get_context()
    existing = await ctx.find_edges_between(
        registry.id, instance.id, edge_class=CATALOGS
    )
    if existing:
        return
    now = cataloged_at or datetime.now(timezone.utc).isoformat()
    await registry.connect(instance, edge=CATALOGS, cataloged_at=now)


async def get_app_attached_content_profile(app_node: App) -> Optional[ContentProfile]:
    """Resolve the single App-attached ContentProfile (formerly App-attached).

    Function name kept as ``get_app_attached_content_profile`` for callsite
    stability; Plan 10-02 (same PR) renames the function to
    ``get_app_attached_content_profile`` across the codebase.
    """
    if getattr(app_node, "attached_content_profile_id", None):
        cp = await ContentProfile.get(app_node.attached_content_profile_id)
        if cp:
            return cp
    children = await app_node.nodes(edge=[HAS_CONTENT_PROFILE], node=["ContentProfile"])
    if children:
        return children[0]  # type: ignore[return-value]
    return None


async def get_track_attached_content_profile(track: Track) -> Optional[ContentProfile]:
    """Resolve the single track-attached ContentProfile."""
    if getattr(track, "attached_content_profile_id", None):
        cp = await ContentProfile.get(track.attached_content_profile_id)
        if cp:
            return cp
    children = await track.nodes(edge=[HAS_CONTENT_PROFILE], node=["ContentProfile"])
    if children:
        return children[0]  # type: ignore[return-value]
    return None


async def get_or_create_views_registry_for_content_profile(
    content_profile: ContentProfile,
    track: Optional[Track] = None,
) -> Views:
    """Return Views registry under a ContentProfile (track-attached or template)."""
    children = await content_profile.nodes(edge=[Edge], node=["Views"])
    if children:
        return cast(Views, children[0])

    now = datetime.now(timezone.utc).isoformat()
    vreg = await Views.create(
        track_id=track.id if track else "",
        content_profile_id=content_profile.id,
        created_at=now,
    )
    await content_profile.connect(vreg)
    return vreg


async def get_or_create_dashboards_registry(app_node: App) -> Dashboards:
    """Return Dashboards registry under an App (I-GRAPH-01)."""
    children = await app_node.nodes(edge=[Edge], node=["Dashboards"])
    if children:
        return cast(Dashboards, children[0])

    now = datetime.now(timezone.utc).isoformat()
    dreg = await Dashboards.create(
        app_id=app_node.id,
        workspace_id=getattr(app_node, "workspace_id", "") or "",
        created_at=now,
    )
    await app_node.connect(dreg, edge=CONTAINS)
    return dreg


async def ensure_app_attached_content_profile(app_node: App) -> ContentProfile:
    """Create Default ContentProfile and HAS_CONTENT_PROFILE if missing."""
    existing = await get_app_attached_content_profile(app_node)
    if existing:
        if not app_node.attached_content_profile_id:
            app_node.attached_content_profile_id = existing.id
            await app_node.save()
        return existing

    now = datetime.now(timezone.utc).isoformat()
    cp = await ContentProfile.create(
        name="Default",
        scope="app",
        manifest={
            "content_profile_schema_version": 2,
            "scope": "app",
            "app": {"tracks": [], "relations": [], "defaults": {}},
            "package": {},
            "migrations": [],
        },
        app_id=app_node.id,
        library_package=False,
        created_at=now,
        updated_at=now,
    )
    await app_node.connect(cp, edge=HAS_CONTENT_PROFILE, attached_at=now)
    app_node.attached_content_profile_id = cp.id
    await app_node.save()
    return cp


async def ensure_track_attached_content_profile(
    track: Track,
    *,
    skip_default_bootstrap: Optional[bool] = None,
) -> ContentProfile:
    """Create attached ContentProfile for a track.

    Default path materializes Post entry type + Feed view. When
    ``skip_default_bootstrap=True`` (bundle-prescribed tracks with
    ``template_id``), creates a minimal empty shell so manifest merge
    owns entry types and views without fighting the Post/Feed bootstrap.

    ``skip_default_bootstrap`` defaults to auto-detecting off ``track.template_id``
    when the caller doesn't pass an explicit value. Most call sites (entries.py,
    entry_types.py, tags.py, agent_scratch.py, track_service.py, entry_listing.py)
    never opted into the templated-track skip, so a bundle-prescribed track that
    got raced by any of those paths before its manifest merge ran would get
    contaminated with the generic Post entry type + Feed view alongside its real
    manifest content. Deriving the default here closes that gap for every caller
    at once instead of requiring each one to remember to pass the flag.
    """
    existing = await get_track_attached_content_profile(track)
    if existing:
        if not track.attached_content_profile_id:
            track.attached_content_profile_id = existing.id
            await track.save()
        return existing

    if skip_default_bootstrap is None:
        skip_default_bootstrap = bool(getattr(track, "template_id", None))

    now = datetime.now(timezone.utc).isoformat()
    if skip_default_bootstrap:
        track_tier: Dict[str, Any] = {
            "entry_types": [],
            "views": [],
            "taxonomy": {"tag_groups": []},
            "defaults": {},
            # This shell is a placeholder that manifest merge (below, or the
            # caller's own subsequent apply_space_track_spec_to_track) owns
            # entirely — it must never independently decide to show a Feed
            # view. Without this, compiling this bare shell through
            # compile_canonical_manifest (which _merge_track_tier_into_manifest
            # does, to read back "what views already exist here" before
            # unioning in the real manifest content) invents a default Feed
            # view via the normal no-opinion fallback — and since that union
            # is additive (never removes a key the new tier doesn't
            # mention), a real manifest declaring suppress_feed_fallback:
            # true could never get rid of it. Found live: a bundle-
            # prescribed Settings track it applied to came back with a
            # visible Feed tab despite its own suppress_feed_fallback: true.
            "suppress_feed_fallback": True,
        }
    else:
        track_tier = {
            "entry_types": [
                {
                    "key": "post",
                    "name": "Post",
                    "icon": "document",
                    "fields": [],
                    "required_tag_groups": [],
                }
            ],
            "views": [
                {
                    "key": "feed",
                    "name": "Feed",
                    "view_type": "feed",
                    "is_default": True,
                    "filters": [],
                    "sort": [],
                    "group_by": None,
                    "layout": {},
                    "field_visibility": [],
                    "kanban_columns": [],
                    "calendar_mapping": {},
                    "config": {},
                }
            ],
            "taxonomy": {"tag_groups": []},
            "defaults": {
                "default_entry_type": "post",
                "default_view": "feed",
            },
        }
    cp = await ContentProfile.create(
        name="Default",
        scope="track",
        manifest={
            "content_profile_schema_version": 2,
            "scope": "track",
            "track": track_tier,
            "package": {},
            "migrations": [],
        },
        library_package=False,
        created_at=now,
        updated_at=now,
    )
    await track.connect(cp, edge=HAS_CONTENT_PROFILE, attached_at=now)
    track.attached_content_profile_id = cp.id
    await track.save()

    if skip_default_bootstrap:
        await get_or_create_views_registry_for_content_profile(cp, track)
        return cp

    post = await EntryType.create(
        name="Post",
        icon="document",
        form_schema={},
        track_id=track.id,
        is_template=False,
        created_at=now,
        updated_at=now,
    )
    await cp.connect(post, edge=CONTAINS, added_at=now)

    vreg = await get_or_create_views_registry_for_content_profile(cp, track)
    from app.services.content_profile_runtime import normalize_view_config

    feed_config = normalize_view_config(
        "feed",
        {"_manifest_view_key": "feed", "view_type": "feed"},
    )
    feed = await View.create(
        name="Feed",
        type="feed",
        config=feed_config,
        track_id=track.id,
        content_profile_id=cp.id,
        is_template=False,
        is_default=True,
        created_by=track.owner_id or "",
        created_at=now,
        updated_at=now,
    )
    await ensure_catalog_edge(vreg, feed)

    return cp


async def ensure_integral_app_graph(
    *,
    include_library: bool = True,
    previous_index: Optional[Dict[str, Dict[str, Any]]] = None,
) -> None:
    """Create app shell: Root→IntegralApp and IntegralApp→registry nodes (unnamed edges).

    When ``include_library`` is False, only the rooted shell and registries are
    ensured — disk library packages are not synced. Tests use this fast path by
    default and opt in via ``@pytest.mark.library`` or an explicit call with
    ``include_library=True``.
    """
    try:
        root = await Root.get()
        now = datetime.now(timezone.utc).isoformat()

        app = await _ensure_fixed_node(
            IntegralApp,
            APP_NODE_ID,
            name="Integral",
            settings={},
            metadata={},
            created_at=now,
            updated_at=now,
        )
        ctx = await root.get_context()
        if not await ctx.find_edges_between(root.id, app.id, edge_class=Edge):
            await root.connect(app)

        users_r = await _ensure_fixed_node(Users, USERS_REGISTRY_ID, created_at=now)
        cps_r = await _ensure_fixed_node(
            ContentProfiles, CONTENT_PROFILES_REGISTRY_ID, created_at=now
        )
        invitations_r = await _ensure_fixed_node(
            Invitations, INVITATIONS_REGISTRY_ID, created_at=now
        )
        workspaces_r = await _ensure_fixed_node(
            Workspaces, WORKSPACES_REGISTRY_ID, created_at=now
        )

        for reg in (
            users_r,
            cps_r,
            invitations_r,
            workspaces_r,
        ):
            if not await ctx.find_edges_between(app.id, reg.id, edge_class=Edge):
                await app.connect(reg)

        if include_library:
            await sync_library_catalog_from_disk(
                catalog_registry=cps_r,
                ensure_catalog_edge=ensure_catalog_edge,
                previous_index=previous_index or {},
                now_iso=now,
                use_cached_specs=os.getenv("TESTING") == "1",
            )
    except Exception:
        logger.exception("ensure_integral_app_graph failed")


async def ensure_library_catalog_seeded(
    *,
    previous_index: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Sync disk library packages into the ContentProfiles registry.

    Idempotent when ``previous_index`` reflects the current catalog. Tests call
    this via the ``library_catalog_seeded`` fixture or ``@pytest.mark.library``.
    """
    now = datetime.now(timezone.utc).isoformat()
    cps_r = await ContentProfiles.get(CONTENT_PROFILES_REGISTRY_ID)
    if cps_r is None:
        await ensure_integral_app_graph(include_library=False)
        cps_r = await ContentProfiles.get(CONTENT_PROFILES_REGISTRY_ID)
    if cps_r is None:
        return {"added": [], "updated": [], "removed": [], "issues": [], "index": {}}
    return await sync_library_catalog_from_disk(
        catalog_registry=cps_r,
        ensure_catalog_edge=ensure_catalog_edge,
        previous_index=previous_index or {},
        now_iso=now,
        use_cached_specs=os.getenv("TESTING") == "1",
    )


# --- Per-workspace branch registries ---------------------------------------

# Branch ids are deterministic — derived from the workspace id — so any
# code path can resolve a Workspace's branch registry without first
# walking edges. The format mirrors the top-level ``n.Class.integral``
# convention.
_BRANCH_KINDS = ("apps", "tracks", "chat_threads")
_BRANCH_CLASSES: dict[str, Type[Node]] = {
    "apps": Apps,
    "tracks": Tracks,
    "chat_threads": ChatThreads,
}


def _workspace_branch_id(workspace_id: str, kind: str) -> str:
    """Return the deterministic id for a Workspace's branch registry.

    ``kind`` ∈ {"apps", "tracks", "chat_threads"}. The class name is
    embedded in the id so jvspatial entity routing matches the persisted
    row when ``Node.get`` is called by id alone.
    """
    cls = _BRANCH_CLASSES[kind]
    return f"n.{cls.__name__}.{workspace_id}"


async def ensure_workspace_branches(
    workspace: Workspace,
) -> tuple[Apps, Tracks, ChatThreads]:
    """Create per-workspace branch registries if missing.

    Idempotent. Ensures the three ``Workspace—CONTAINS→Branch`` edges
    exist; safe to call on every workspace-write path.
    """
    now = datetime.now(timezone.utc).isoformat()
    branches: dict[str, Node] = {}
    for kind in _BRANCH_KINDS:
        cls = _BRANCH_CLASSES[kind]
        branch = await _ensure_fixed_node(
            cls,
            _workspace_branch_id(workspace.id, kind),
            workspace_id=workspace.id,
            created_at=now,
        )
        branches[kind] = branch
        ctx = await workspace.get_context()
        existing = await ctx.find_edges_between(
            workspace.id, branch.id, edge_class=CONTAINS
        )
        if not existing:
            await workspace.connect(branch, edge=CONTAINS, added_at=now)
    return (
        cast(Apps, branches["apps"]),
        cast(Tracks, branches["tracks"]),
        cast(ChatThreads, branches["chat_threads"]),
    )


async def _resolve_workspace_branch(workspace_id: str, kind: str) -> Optional[Node]:
    """Resolve a workspace's branch registry; lazy-create on miss.

    Returns ``None`` only if the parent Workspace itself is gone.
    """
    if not workspace_id:
        return None
    cls = _BRANCH_CLASSES[kind]
    branch = await cls.get(_workspace_branch_id(workspace_id, kind))
    if branch:
        return branch
    parent = await Workspace.get(workspace_id)
    if not parent:
        return None
    spaces_b, tracks_b, threads_b = await ensure_workspace_branches(parent)
    return {"apps": spaces_b, "tracks": tracks_b, "chat_threads": threads_b}[kind]


# --- Top-level catalog helpers --------------------------------------------


async def catalog_user(user: User) -> None:
    """Register a User under the app Users registry."""
    reg = await Users.get(USERS_REGISTRY_ID)
    if reg:
        await ensure_catalog_edge(reg, user)


async def catalog_app(app_node: App) -> None:
    """Register an App (formerly App) under its Workspace's branch and ensure its CP.

    Function name kept as ``catalog_app`` for callsite stability;
    Plan 10-02 (same PR) renames the function to ``catalog_app`` across
    the codebase.
    """
    workspace_id = getattr(app_node, "workspace_id", "") or ""
    if workspace_id:
        branch = await _resolve_workspace_branch(workspace_id, "apps")
        if branch:
            await ensure_catalog_edge(branch, app_node)
        else:
            logger.warning(
                "catalog_app: workspace %s missing; skipping branch write for %s",
                workspace_id,
                app_node.id,
            )
    else:
        logger.warning(
            "catalog_app: space %s has no workspace_id; skipping branch write",
            app_node.id,
        )
    await ensure_app_attached_content_profile(app_node)


class AppOwnerWireError(RuntimeError):
    """No principal could be resolved to own an App. Never swallow this.

    An App with no ``OWNS`` edge is administrable by nobody. Org workspace
    owners resolve only to the implicit staff ``commenter``
    (``workspace_staff_implicit_resource_role`` — read and participate by
    design, never authority), so ``can_admin_app`` is False for every human
    alive and there is no UI path back. Failing the install is recoverable; a
    silently ownerless App is not.
    """


async def wire_app_owner(
    app: App,
    user_id: str,
    *,
    workspace_id: str = "",
) -> None:
    """Wire OWNS + workspace_id + owner_user_id on App create or idempotent reuse.

    Raises ``AppOwnerWireError`` when no owner can be established. This used to
    return silently when ``user_id`` did not resolve to a graph profile (an
    ``o.User.*`` principal whose ``User`` node is missing, or an empty actor on
    a system-initiated install), which produced Apps nobody could administer —
    observed in a dev workspace as an active App with zero ``OWNS`` edges whose
    ``owner_user_id`` still named a principal, i.e. the field claimed an owner
    the graph did not have.
    """
    from app.services.permissions import get_user_node
    from app.services.workspace_permissions import get_workspace_owner_user_id

    user = await get_user_node(user_id) if user_id else None
    if user is None:
        # Fall back to the workspace owner rather than leaving the App
        # unadministrable. They are precisely the principal who would
        # otherwise be locked out of their own workspace's App.
        ws_id = workspace_id or str(getattr(app, "workspace_id", "") or "").strip()
        fallback_id = await get_workspace_owner_user_id(ws_id) if ws_id else None
        user = await get_user_node(fallback_id) if fallback_id else None
        if user is not None:
            logger.warning(
                "wire_app_owner: actor %r did not resolve; falling back to "
                "workspace owner %s for app %s",
                user_id,
                user.id,
                app.id,
            )
    if user is None:
        raise AppOwnerWireError(
            f"Cannot establish an owner for app {app.id!r}: actor {user_id!r} "
            "did not resolve and the workspace has no owner"
        )

    now = datetime.now(timezone.utc).isoformat()
    ctx = await user.get_context()
    owns = await ctx.find_edges_between(user.id, app.id, edge_class=OWNS)
    collab = await ctx.find_edges_between(user.id, app.id, edge_class=COLLABORATES_ON)
    if not owns and not collab:
        await user.connect(app, edge=OWNS, role="owner", granted_at=now)
    repaired = False
    if workspace_id and not str(getattr(app, "workspace_id", "") or "").strip():
        app.workspace_id = workspace_id
        repaired = True
    if not str(getattr(app, "owner_user_id", "") or "").strip():
        # Store the GRAPH profile id, not whatever id the caller happened to
        # hold. `resolve_role` reads edges, which are keyed by the graph node;
        # stamping an `o.User.*` principal here made the field disagree with
        # the graph and read as an owner who had no grant.
        app.owner_user_id = user.id
        repaired = True
    if repaired:
        app.updated_at = now
        await app.save()


async def catalog_workspace(workspace: Workspace) -> None:
    """Register a Workspace under the app Workspaces registry + ensure branches."""
    reg = await Workspaces.get(WORKSPACES_REGISTRY_ID)
    if reg:
        await ensure_catalog_edge(reg, workspace)
    await ensure_workspace_branches(workspace)


async def catalog_invitation(invitation: Invitation) -> None:
    """Register an Invitation under the app Invitations registry."""
    reg = await Invitations.get(INVITATIONS_REGISTRY_ID)
    if reg:
        await ensure_catalog_edge(reg, invitation)


async def catalog_track(track: Track) -> None:
    """Register a Track under its Workspace's branch and ensure its CP."""
    workspace_id = getattr(track, "workspace_id", "") or ""
    if workspace_id:
        branch = await _resolve_workspace_branch(workspace_id, "tracks")
        if branch:
            await ensure_catalog_edge(branch, track)
        else:
            logger.warning(
                "catalog_track: workspace %s missing; skipping branch write for %s",
                workspace_id,
                track.id,
            )
    else:
        logger.warning(
            "catalog_track: track %s has no workspace_id; skipping branch write",
            track.id,
        )
    await ensure_track_attached_content_profile(track)


async def catalog_chat_thread(thread: ChatThread) -> None:
    """Register a ChatThread under its Workspace's branch registry."""
    workspace_id = getattr(thread, "workspace_id", "") or ""
    if not workspace_id:
        logger.warning(
            "catalog_chat_thread: thread %s has no workspace_id; skipping",
            getattr(thread, "id", "?"),
        )
        return
    branch = await _resolve_workspace_branch(workspace_id, "chat_threads")
    if branch:
        await ensure_catalog_edge(branch, thread)


async def catalog_view_under_track(track: Track, view: View) -> None:
    """Catalog a View under the track's attached ContentProfile Views registry."""
    cp = await get_track_attached_content_profile(track)
    if not cp:
        cp = await ensure_track_attached_content_profile(track)
    vreg = await get_or_create_views_registry_for_content_profile(cp, track)
    await ensure_catalog_edge(vreg, view)


async def catalog_template_node_under_content_profile(
    content_profile: ContentProfile, node: Node
) -> None:
    """Attach template EntryType, Tag, or View under a ContentProfile via CATALOGS."""
    await ensure_catalog_edge(content_profile, node)


async def link_notification(user: User, notification: Notification) -> None:
    """Connect a notification to the user if not already linked."""
    from app.models.edges import HAS_NOTIFICATION

    ctx = await user.get_context()
    if await ctx.find_edges_between(
        user.id, notification.id, edge_class=HAS_NOTIFICATION
    ):
        return
    await user.connect(
        notification,
        edge=HAS_NOTIFICATION,
        created_at=notification.created_at or datetime.now(timezone.utc).isoformat(),
    )


async def create_notification(
    *,
    user_id: str,
    **fields: Any,
) -> Notification:
    """Create a Notification AND wire User —HAS_NOTIFICATION→ Notification.

    Canonical helper for I-GRAPH-01 compliance (Phase 10.5 Plan 10.5-01).
    Every code path that persists a Notification MUST go through this
    helper — direct ``Notification.create(...)`` is forbidden as of
    2026-05-20 (caught by the AST gate in
    ``backend/tests/test_graph_contiguousness.py``).

    The User lookup is mandatory — if the recipient User node is
    missing (e.g. a synthetic / service-only id), the Notification row
    cannot be wired to a rooted ancestor, so the helper rolls back the
    persisted node and raises ``ValueError``. An unwired Notification
    would violate I-GRAPH-01; refusing it at the source is stricter
    than the pre-Plan-10.5-01 best-effort semantics it replaced.

    Args:
        user_id: ``User.user_id`` (auth subject) — used to resolve the
            User node by walking the Users registry.
        **fields: Forwarded to ``Notification.create(...)`` verbatim;
            callers populate ``type``, ``content``, ``metadata``, etc.

    Returns:
        The persisted, edge-wired Notification node.
    """
    # Auto-stamp created_at if the caller didn't supply one. Matches
    # the legacy behavior of ``notification_router._persist`` /
    # ``sharing._emit_share_notification`` (both set their own
    # created_at scalars).
    if "created_at" not in fields:
        fields["created_at"] = datetime.now(timezone.utc).isoformat()

    from app.services.permissions import get_user_node

    recipient = await get_user_node(user_id)
    if recipient is None:
        raise ValueError(
            f"create_notification: user node missing for {user_id!r} — "
            "refusing to persist unwired Notification"
        )

    # Callers may pass either the graph User node id or the auth subject
    # (User.user_id). GET /notifications queries by JWT principal, so persist
    # the auth subject whenever the resolved node carries one.
    persist_user_id = (recipient.user_id or "").strip() or user_id

    notification = await Notification.create(user_id=persist_user_id, **fields)
    try:
        await link_notification(recipient, notification)
    except Exception as exc:
        logger.exception(
            "create_notification: link_notification failed for user=%s "
            "notification=%s",
            user_id,
            notification.id,
        )
        try:
            await notification.delete()
        except Exception:
            logger.exception(
                "create_notification: rollback delete failed for notification=%s",
                notification.id,
            )
        raise RuntimeError(
            "create_notification: could not wire HAS_NOTIFICATION edge"
        ) from exc
    return notification
