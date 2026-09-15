"""Personal Context App provisioning — one per user, in their own workspace.

Every user gets this App when their personal workspace is created, so the
very first turn they take is already being observed. Lazy provisioning at
first agent connect (the ``agent_scratch`` pattern) would miss it.

Idempotence has three layers, from cheapest to most authoritative:

1. a per-process id cache, keyed by user (the same shape
   ``agent_scratch`` uses — at most one entry per user, invalidated on a
   stale hit rather than evicted);
2. a graph walk for an existing install of this bundle in the workspace;
3. ``install_app`` itself, which short-circuits on an already-active
   bundle install and returns the same app id.

So a second signup call, a retry, and a concurrent first-read all
converge on one App.

``settings={}`` is passed deliberately. ``install_app`` pauses at
``awaiting_settings`` when a manifest declares ``settings_schema`` and the
caller supplies nothing — correct for a human installing from the
catalog, wrong here, where there is nobody to answer. An empty dict means
"take the manifest's defaults", which for this bundle is: attention on,
nothing excluded, promotions batched into one card a day, one question a
day.

Provisioning NEVER raises into its caller. Signup must not fail because
an App did not install; the user simply has no Personal Context yet, and
the next call provisions it.

I-GRAPH-01: this module creates no nodes of its own. Every node minted
here comes from ``install_app``, which wires the App into
``Workspace —CONTAINS→ App`` and its tracks under it inside its own
transaction.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.models.nodes import App, ContentProfile
from app.services.core_seed_installer import (
    install_core_package,
    resolve_core_package_library,
)
from app.services.personal_workspace import ensure_personal_workspace
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bundle identity — declared ONCE (directory + package.slug + I-BUNDLE-04)
# ---------------------------------------------------------------------------

#: Directory + package slug of the bundle (``app/profiles/<slug>/``).
PERSONAL_CONTEXT_SLUG = "personal-context"

#: Display name shown in the sidebar. Only used as a lookup fallback for
#: library rows synced before ``metadata.slug`` was recorded.
PERSONAL_CONTEXT_NAME = "Personal Context"

# user_id -> app_id. Mutated under the asyncio single-thread scheduler;
# no lock needed. Cleared between tests by ``reset_personal_context_cache``.
_APP_ID_CACHE: Dict[str, str] = {}


def reset_personal_context_cache() -> None:
    """Drop the per-process cache. Test hook; also safe in production."""
    _APP_ID_CACHE.clear()


async def _resolve_library_package() -> Optional[ContentProfile]:
    """Find the seeded library ContentProfile for this core package."""
    return await resolve_core_package_library(
        slug=PERSONAL_CONTEXT_SLUG,
        fallback_name=PERSONAL_CONTEXT_NAME,
    )


async def _find_existing_app(workspace_id: str) -> Optional[App]:
    """Return this bundle's App in ``workspace_id``, or ``None``.

    Matches on ``App.source_profile_slug`` — the discriminator
    ``install_app`` stamps from ``package.slug`` — rather than on the
    App's title, which the user may rename.
    """
    try:
        found = await App.find({"context.source_profile_slug": PERSONAL_CONTEXT_SLUG})
    except Exception:  # noqa: BLE001
        logger.exception("personal-context install lookup failed")
        return None
    for app_node in list(found or []):
        if getattr(app_node, "workspace_id", "") == workspace_id:
            return app_node
    return None


async def provision_personal_context_app(*, user_id: str) -> Optional[App]:
    """Return the user's Personal Context App, installing it if missing.

    Returns ``None`` — never raises — when the user cannot be resolved,
    the library package has not been seeded, or the install fails. Every
    one of those is logged and retried on the next call.
    """
    cached_id = _APP_ID_CACHE.get(user_id)
    if cached_id:
        cached = await App.get(cached_id)
        if cached is not None and (
            getattr(cached, "source_profile_slug", "") == PERSONAL_CONTEXT_SLUG
        ):
            return cached
        _APP_ID_CACHE.pop(user_id, None)

    from app.services.permissions import get_user_node

    user = await get_user_node(user_id)
    if user is None:
        logger.warning("personal-context: user %s not found; skipping", user_id)
        return None

    try:
        workspace = await ensure_personal_workspace(user)
    except Exception:  # noqa: BLE001
        logger.exception("personal-context: personal workspace unavailable")
        return None

    existing = await _find_existing_app(workspace.id)
    if existing is not None:
        _APP_ID_CACHE[user_id] = existing.id
        await _sync_operational_layer_if_manifest_moved(existing)
        return existing

    library = await _resolve_library_package()
    if library is None:
        logger.warning(
            "personal-context: library package %r not seeded yet; "
            "provisioning deferred",
            PERSONAL_CONTEXT_SLUG,
        )
        return None

    try:
        app_id = await install_core_package(
            slug=PERSONAL_CONTEXT_SLUG,
            workspace_id=workspace.id,
            actor_id=user.id,
            fallback_name=PERSONAL_CONTEXT_NAME,
            settings={},
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "personal-context: install failed for user %s (workspace %s)",
            user.id,
            workspace.id,
        )
        return None

    if not app_id:
        logger.warning("personal-context: install returned no app id")
        return None
    app_node = await App.get(app_id)
    if app_node is None:
        return None
    if getattr(app_node, "lifecycle_state", "") != "active":
        # Defensive: settings={} should always reach "active". If a future
        # manifest edit adds a required setting with no default, say so
        # loudly rather than caching a half-installed App.
        logger.warning(
            "personal-context: install for %s ended at %r, not active",
            user.id,
            getattr(app_node, "lifecycle_state", None),
        )
        return None
    _APP_ID_CACHE[user_id] = app_node.id
    logger.info(
        "personal-context: provisioned app %s for user %s at %s",
        app_node.id,
        user.id,
        utc_now_iso(),
    )
    return app_node


async def resolve_personal_context(*, user_id: str) -> Optional[Dict[str, Any]]:
    """Everything a caller needs to write into a person's Personal Context.

    Returns ``{"app", "workspace_id", "settings", "tracks", "bundle_dir"}``
    where ``tracks`` maps MANIFEST TRACK KEY -> Track node (``stream``,
    ``attention``, ``identity``, …), or ``None`` when the person has no App,
    has switched attention off, or anything in the chain is missing.

    Exists so callers outside the substrate — notably the resident's
    post-turn attention action — do not traverse the graph themselves. They
    ask for the target and write through the agent dispatch seam; the walk
    from App to tracks, and the settings gate, live here where they can be
    tested once.

    ``bundle_dir`` is the on-disk directory the App was installed from,
    recorded on the library ContentProfile at sync. Callers needing the
    bundle's own SOP text read it from there rather than holding a path to a
    bundle they do not own.
    """
    from app.models.edges import CONTAINS

    app_node = await provision_personal_context_app(user_id=user_id)
    if app_node is None:
        return None

    settings = dict(getattr(app_node, "settings", None) or {})
    if settings.get("attention_enabled") is False:
        return None

    try:
        tracks = await app_node.nodes(edge=[CONTAINS], node=["Track"], direction="out")
    except Exception:  # noqa: BLE001
        logger.exception("personal-context: track walk failed for %s", app_node.id)
        return None
    by_key = {
        str(getattr(t, "template_id", "") or ""): t
        for t in tracks
        if str(getattr(t, "template_id", "") or "")
    }
    if not by_key:
        return None

    return {
        "app": app_node,
        "workspace_id": str(getattr(app_node, "workspace_id", "") or ""),
        "settings": settings,
        "tracks": by_key,
        "bundle_dir": await _resolve_bundle_dir(),
    }


async def _resolve_bundle_dir() -> str:
    """On-disk directory the bundle was loaded from, per the library row."""
    library = await _resolve_library_package()
    if library is None:
        return ""
    return str((getattr(library, "metadata", None) or {}).get("bundle_dir_path") or "")


async def _sync_operational_layer_if_manifest_moved(app) -> None:
    """Re-sync skills/agents/hooks when the bundle on disk is a newer version.

    This provisioning path returns an already-installed App directly, so it
    never reaches ``install_app`` -- and ``install_app`` is where a reused
    install gets re-materialized against the current manifest. The result was
    that a bundle edit reached a FRESH install and never an existing one: a
    changed SKILL.md ``allowed-tools`` left the persisted Skill node carrying
    its original grant, and the running agent kept following an SOP it no
    longer had the tools to satisfy. Nothing errored, and a manifest version
    bump did not help either.

    Gated on the version so the common path stays a single lookup: an
    unchanged bundle does no work. The sync itself is an idempotent upsert.
    """
    try:
        library = await _resolve_library_package()
        if library is None:
            return
        # The version lives on the library ContentProfile node -- the compiled
        # manifest does not carry it -- and the canonical form is compiled from
        # `manifest`, exactly as install_app does it.
        manifest_version = str(getattr(library, "version", "") or "").strip()
        installed_version = str(getattr(app, "version", "") or "").strip()
        if not manifest_version or manifest_version == installed_version:
            return

        from app.services.content_profile_compile import compile_canonical_manifest

        canonical = compile_canonical_manifest(
            manifest=getattr(library, "manifest", None) or {}
        )

        from app.services.app_lifecycle import (
            _materialize_tracks_for_app,
            sync_operational_layer_from_manifest,
        )

        actor_id = str(getattr(app, "owner_id", "") or "")
        # Tracks and their EntryTypes first, then the operational layer. Both
        # halves drift, and the EntryType half fails the more confusing way:
        # a stale field schema does not error, it makes the write's fields
        # "not allowed", and create_entry's selective-drop retry peels them off
        # one at a time until the entry lands with none of them. Measured: a
        # correction wrote its replacement fact and kept `status` and
        # `superseded_by` empty, so the superseded belief still read as live --
        # "Field 'role' is not allowed for entry type 'Role'" was the only
        # trace, at INFO.
        await _materialize_tracks_for_app(app, actor_id)
        await sync_operational_layer_from_manifest(app, canonical, actor_id=actor_id)
        app.version = manifest_version
        await app.save()
        logger.info(
            "personal-context: synced operational layer %s -> %s for App %s",
            installed_version or "<unset>",
            manifest_version,
            app.id,
        )
    except Exception:  # noqa: BLE001
        # Provisioning must never fail over a refresh -- the App still works
        # with its previous operational layer.
        logger.exception("personal-context: operational-layer sync failed")
