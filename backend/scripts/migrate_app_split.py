"""Phase 31 — Migrate workspaces from crm-plus-pm-suite → 4 bundles.

Decomposes the legacy ``crm-plus-pm-suite`` App into four standalone
Apps: ``crm``, ``sales``, ``projects``, ``portfolio``. Walks each
workspace that has the legacy App installed and:

1. Installs the four new library Apps in topological order
   (projects → crm → sales/portfolio).
2. Re-parents every Track under the old App to its new owning App by
   swapping the ``App-CONTAINS->Track`` edge. EntryType.track_id,
   entries, REFERENCES, COLLABORATES_ON, EXCLUDED_FROM, INVITATION,
   SHARE_LINK edges all stay attached to the Track and ride along.
3. Uninstalls the legacy App after every Track has moved.

Idempotent — re-running after a partial failure skips already-moved
tracks and already-installed new apps. ``--dry-run`` prints the plan
without writing.

Per DR-31-01 §9. Forward-only: no aliasing, no compatibility shim,
no transitional slug remapping.

Usage::

    # Dry run (preview).
    python -m scripts.migrate_app_split --dry-run

    # Single workspace.
    python -m scripts.migrate_app_split --workspace-id n.Workspace.X

    # All workspaces with crm-plus-pm-suite installed.
    python -m scripts.migrate_app_split --all
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
from typing import Dict, List, Optional, Tuple

from app.models.edges import CONTAINS
from app.models.nodes import App, Track, Workspace
from app.services.app_lifecycle import (
    finalize_install,
    get_app_attached_content_profile,
    install_app,
    uninstall_app,
)
from app.services.content_profile_library_sync import sync_library_catalog_from_disk

logger = logging.getLogger(__name__)


LEGACY_SLUG = "crm-plus-pm-suite"
NEW_SLUGS_IN_TOPO_ORDER: Tuple[str, ...] = ("projects", "crm", "sales", "portfolio")

NEW_APP_NAMES: Dict[str, str] = {
    "crm": "CRM",
    "sales": "Sales",
    "projects": "Projects",
    "portfolio": "Portfolio",
}

# Track-title slug → new bundle slug. Slug = lowercase, spaces→underscore,
# strip non-alphanumeric. Per-project anchor children (Project Details:
# X, Contracts & Legal: X, Project Financials: X) match by prefix.
TRACK_PREFIX_ROUTES: Tuple[Tuple[str, str], ...] = (
    ("project:", "projects"),
    ("project details:", "projects"),
    ("contracts & legal:", "projects"),
    ("project financials:", "projects"),
    ("email messages:", "crm"),
)

TRACK_EXACT_ROUTES: Dict[str, str] = {
    "contacts": "crm",
    "opportunities": "crm",
    "communications": "crm",
    "discovery sessions": "sales",
    "scoping documents": "sales",
    "pricing rubrics": "sales",
    "project proposals": "sales",
    "projects": "projects",
    "portfolio": "portfolio",
}


def _route_track_to_bundle(track_title: str) -> Optional[str]:
    """Map a Track.title to its new owning bundle slug.

    Returns None for tracks that do NOT belong to the legacy bundle's
    surface (caller logs + skips). Prefix routes win over exact routes
    because per-project anchor children carry the project name as a
    suffix and must route by prefix.
    """
    norm = (track_title or "").strip().lower()
    for prefix, slug in TRACK_PREFIX_ROUTES:
        if norm.startswith(prefix):
            return slug
    return TRACK_EXACT_ROUTES.get(norm)


async def _find_library_id_by_slug(slug: str) -> Optional[str]:
    """Look up a library ContentProfile by package.slug."""
    from app.models.nodes import ContentProfile

    candidates = await ContentProfile.find({"library_package": True})
    for cp in candidates:
        pkg = (cp.manifest or {}).get("package") or {}
        if pkg.get("slug") == slug:
            return cp.id
    return None


async def _find_app_by_slug(workspace_id: str, slug: str) -> Optional[App]:
    """Find an installed App in a workspace by manifest slug.

    App nodes don't carry the slug directly; resolve via the attached
    ContentProfile manifest.
    """
    apps = await App.find({"workspace_id": workspace_id})
    for app_node in apps:
        cp = await get_app_attached_content_profile(app_node)
        if cp is None:
            continue
        pkg = (cp.manifest or {}).get("package") or {}
        if pkg.get("slug") == slug:
            return app_node
    return None


async def _tracks_under_app(app_node: App) -> List[Track]:
    """Return every Track currently bound to ``app_node`` via CONTAINS."""
    tracks = await app_node.nodes(edge=[CONTAINS], node=["Track"], direction="out")
    return list(tracks)


async def _swap_track_parent(
    *,
    old_app: App,
    new_app: App,
    track: Track,
    dry_run: bool,
) -> None:
    """Move a Track from old_app to new_app via CONTAINS edge swap.

    Idempotent: skips silently if the track is already a child of new_app
    OR is no longer a child of old_app.

    I-GRAPH-01: the disconnect+connect happens in the same function so
    the Track is never structurally detached between transactions.
    """
    ctx = await track.get_context()

    # Idempotency: is the track already under new_app?
    new_edges = await ctx.find_edges_between(new_app, track, edge_class=CONTAINS)
    if new_edges:
        logger.info(
            "  track %s already under new app %s (slug=%s); skipping",
            track.title,
            new_app.id,
            (await get_app_attached_content_profile(new_app))
            .manifest.get("package", {})
            .get("slug"),
        )
        return

    old_edges = await ctx.find_edges_between(old_app, track, edge_class=CONTAINS)

    if dry_run:
        logger.info(
            "  [DRY] would re-parent track %r (%s) from %s → %s",
            track.title,
            track.id,
            old_app.id,
            new_app.id,
        )
        return

    # Delete the old edge first (each is a single edge instance).
    for e in old_edges:
        await e.delete()

    # Wire the new structural parent (I-GRAPH-01).
    await new_app.connect(track, edge=CONTAINS)

    logger.info(
        "  re-parented track %r from %s → %s",
        track.title,
        old_app.id,
        new_app.id,
    )


async def _install_or_get(
    *,
    workspace_id: str,
    slug: str,
    actor_id: str,
    dry_run: bool,
) -> Optional[App]:
    """Install a new bundle App if absent; return the App node either way."""
    existing = await _find_app_by_slug(workspace_id, slug)
    if existing is not None:
        logger.info(
            "  app %s already installed in workspace %s; reusing", slug, workspace_id
        )
        return existing

    if dry_run:
        logger.info(
            "  [DRY] would install bundle %s in workspace %s", slug, workspace_id
        )
        return None

    lib_id = await _find_library_id_by_slug(slug)
    if lib_id is None:
        raise RuntimeError(
            f"library ContentProfile not found for slug {slug!r}; "
            "run library sync first."
        )

    result = await install_app(
        workspace_id=workspace_id,
        library_cp_id=lib_id,
        actor_id=actor_id,
    )
    if result.get("status") == "awaiting_settings":
        # None of the four new bundles declare a settings_schema, so
        # this branch is defensive — if a future bundle adds settings,
        # the migration needs an explicit settings payload.
        await finalize_install(
            app_id=result["app_id"],
            install_token=result["install_token"],
            actor_id=actor_id,
            settings={},
        )
    logger.info(
        "  installed bundle %s (app_id=%s) in workspace %s",
        slug,
        result.get("app_id"),
        workspace_id,
    )
    return await _find_app_by_slug(workspace_id, slug)


async def migrate_one_workspace(
    *,
    workspace_id: str,
    actor_id: str,
    dry_run: bool,
) -> Dict[str, int]:
    """Run the full migration for a single workspace.

    Returns a counter dict ``{installed, reparented, skipped, uninstalled}``.
    """
    counters = {"installed": 0, "reparented": 0, "skipped": 0, "uninstalled": 0}

    old_app = await _find_app_by_slug(workspace_id, LEGACY_SLUG)
    if old_app is None:
        logger.info(
            "workspace %s: no %s App installed; skipping", workspace_id, LEGACY_SLUG
        )
        return counters

    logger.info(
        "workspace %s: legacy app %s present (app_id=%s)",
        workspace_id,
        LEGACY_SLUG,
        old_app.id,
    )

    # Step 1: install the four new apps in topological order.
    new_apps: Dict[str, App] = {}
    for slug in NEW_SLUGS_IN_TOPO_ORDER:
        new_app = await _install_or_get(
            workspace_id=workspace_id, slug=slug, actor_id=actor_id, dry_run=dry_run
        )
        if new_app is not None:
            new_apps[slug] = new_app
            counters["installed"] += 1

    if dry_run:
        # In dry-run we don't have the new App nodes, so we can only
        # preview the routing decisions, not actually move tracks.
        for track in await _tracks_under_app(old_app):
            route = _route_track_to_bundle(track.title or "")
            if route is None:
                logger.info("  [DRY] track %r — UNROUTED (would skip)", track.title)
                counters["skipped"] += 1
            else:
                logger.info("  [DRY] track %r → bundle %s", track.title, route)
                counters["reparented"] += 1
        logger.info(
            "workspace %s: [DRY] would uninstall %s after reparent",
            workspace_id,
            LEGACY_SLUG,
        )
        return counters

    # Step 2: re-parent every Track currently under the legacy App.
    for track in await _tracks_under_app(old_app):
        route = _route_track_to_bundle(track.title or "")
        if route is None:
            logger.warning(
                "  track %r — no route in TRACK_EXACT_ROUTES / TRACK_PREFIX_ROUTES; "
                "leaving it under the legacy App. Manual review required.",
                track.title,
            )
            counters["skipped"] += 1
            continue
        new_app = new_apps.get(route)
        if new_app is None:
            logger.warning(
                "  track %r → bundle %s, but new App not installed; skipping",
                track.title,
                route,
            )
            counters["skipped"] += 1
            continue
        await _swap_track_parent(
            old_app=old_app, new_app=new_app, track=track, dry_run=False
        )
        counters["reparented"] += 1

    # Step 3: confirm legacy App has no remaining children, then uninstall.
    remaining = await _tracks_under_app(old_app)
    if remaining:
        logger.warning(
            "workspace %s: legacy app %s still contains %d tracks (%s); skipping uninstall",
            workspace_id,
            LEGACY_SLUG,
            len(remaining),
            [t.title for t in remaining],
        )
        return counters

    await uninstall_app(app_id=old_app.id, actor_id=actor_id)
    counters["uninstalled"] = 1
    logger.info("workspace %s: uninstalled legacy app %s", workspace_id, LEGACY_SLUG)
    return counters


async def _find_workspaces_with_legacy() -> List[str]:
    """Return the IDs of every Workspace that has the legacy App installed."""
    affected: List[str] = []
    workspaces = await Workspace.find({})
    for ws in workspaces:
        if await _find_app_by_slug(ws.id, LEGACY_SLUG) is not None:
            affected.append(ws.id)
    return affected


async def _resolve_actor_id(workspace_id: str) -> str:
    """Pick a viable actor_id for the install/uninstall calls.

    Prefers a workspace owner; falls back to admin role; final fallback
    is the literal string ``system`` (matches the convention used in
    the seed_data + migration scripts).
    """
    ws = await Workspace.get(workspace_id)
    if ws is None:
        return "system"
    # Walk OWNS/IS_MEMBER_OF edges to find a user.
    from app.models.edges import IS_MEMBER_OF, OWNS

    for edge_cls in (OWNS, IS_MEMBER_OF):
        users = await ws.nodes(edge=[edge_cls], node=["User"], direction="in")
        if users:
            return users[0].id
    return "system"


async def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 31 — App split migration")
    parser.add_argument(
        "--workspace-id",
        help="Run migration for a single workspace ID",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run migration for every workspace that has crm-plus-pm-suite installed",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned moves without writing",
    )
    parser.add_argument(
        "--skip-library-sync",
        action="store_true",
        help="Skip the disk → library ContentProfile sync (use when already in sync)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from app.services.db_init import init_prime_db

    init_prime_db()

    if not args.workspace_id and not args.all:
        parser.error("Specify --workspace-id or --all")

    if not args.skip_library_sync:
        logger.info("Syncing library catalog from disk…")
        from app.services.app_graph import ensure_library_catalog_seeded

        await ensure_library_catalog_seeded()

    if args.workspace_id:
        workspaces = [args.workspace_id]
    else:
        workspaces = await _find_workspaces_with_legacy()
        logger.info(
            "Found %d workspace(s) with %s installed", len(workspaces), LEGACY_SLUG
        )

    total = {"installed": 0, "reparented": 0, "skipped": 0, "uninstalled": 0}
    for ws_id in workspaces:
        actor_id = await _resolve_actor_id(ws_id)
        per_ws = await migrate_one_workspace(
            workspace_id=ws_id, actor_id=actor_id, dry_run=args.dry_run
        )
        for k, v in per_ws.items():
            total[k] += v

    logger.info(
        "Migration complete (dry_run=%s). Installed=%d Reparented=%d Skipped=%d UninstalledLegacy=%d",
        args.dry_run,
        total["installed"],
        total["reparented"],
        total["skipped"],
        total["uninstalled"],
    )


if __name__ == "__main__":
    asyncio.run(main())
