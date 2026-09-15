"""Upgrade existing Projects installs (planner buckets + Sprints track).

Reconciles materialized graph state after Projects library yaml changes
(``project-details`` Task fields including ``sprint``, bucket kanban,
prescribed ``sprints`` track). Safe to run repeatedly — idempotent.

Steps per Projects App:

1. Re-merge the library package into the App attached ContentProfile
   (``update_app_from_library`` — also provisions prescribed tracks such as
   Sprints and refreshes track-template materializations).
2. Upgrade per-anchor-track materialized EntryType nodes from the manifest tier
   so Task gains the Sprint relation field on existing details tracks.
3. Backfill ``custom_fields.bucket`` on existing Task entries (title map, then
   status fallback).
4. Re-sync default view flags on anchored tracks.
5. Materialize EntryTypes / views on the Sprints track when present.

Usage::

    python -m scripts.upgrade_project_details_planner --dry-run
    python -m scripts.upgrade_project_details_planner

No-ops gracefully when no Projects App is installed.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from typing import Dict, List

from app.models.nodes import App, Entry, Track
from app.services.app_graph import get_app_attached_content_profile
from app.services.app_lifecycle import update_app_from_library
from app.services.content_profile_merge import (
    refresh_all_app_track_template_materializations,
)
from app.services.content_profile_runtime import synchronize_track_view_default_flags

logger = logging.getLogger(__name__)

PROJECTS_APP_SLUG = "projects"
PROJECT_DETAILS_TEMPLATE = "project-details"
SPRINTS_TRACK_TITLE = "Sprints"
SPRINTS_TRACK_TYPE_KEY = "sprints"
TASK_ENTRY_TYPE_NAME = "Task"

# Title → bucket from seed fixtures (backend/seed_data.py PROJECT_DETAIL_TASK_SEEDS).
TITLE_TO_BUCKET: Dict[str, str] = {
    "spec api contract": "in_progress",
    "wire frontend route": "this_week",
    "draft regression tests": "backlog",
    "schema migration script": "backlog",
    "document the change in the runbook": "done",
    "verify with stakeholders": "this_week",
}

STATUS_TO_BUCKET: Dict[str, str] = {
    "todo": "backlog",
    "in_progress": "in_progress",
    "in_review": "this_week",
    "done": "done",
}

TITLE_TO_ASSIGNEE_SLOT: Dict[str, int] = {
    "spec api contract": 0,
    "wire frontend route": 1,
    "draft regression tests": 2,
    "schema migration script": 0,
    "document the change in the runbook": 1,
    "verify with stakeholders": 2,
}


async def _find_projects_apps() -> List[App]:
    apps = await App.find()
    if apps is None:
        return []
    if not isinstance(apps, list):
        apps = [apps]
    out: List[App] = []
    seen: set[str] = set()

    def _maybe_add(app: App) -> None:
        if app.id in seen:
            return
        if str(getattr(app, "lifecycle_state", "") or "") == "uninstalled":
            return
        seen.add(app.id)
        out.append(app)

    for app in apps:
        slug = (
            (getattr(app, "attached_content_profile_slug", "") or "").strip().casefold()
        )
        profile_slug = (
            (getattr(app, "source_profile_slug", "") or "").strip().casefold()
        )
        name = str(getattr(app, "name", "") or "").strip().casefold()
        if slug == PROJECTS_APP_SLUG or profile_slug == PROJECTS_APP_SLUG:
            _maybe_add(app)
            continue
        if name == "projects":
            _maybe_add(app)

    if out:
        return out

    for app in apps:
        if str(getattr(app, "lifecycle_state", "") or "") == "uninstalled":
            continue
        tracks = await app.nodes(edge=["CONTAINS"], direction="out", node=["Track"])
        for t in tracks:
            if (
                isinstance(t, Track)
                and str(getattr(t, "template_id", "") or "") == PROJECT_DETAILS_TEMPLATE
            ):
                _maybe_add(app)
                break
    return out


async def _project_details_anchor_tracks(app: App) -> List[Track]:
    tracks = await app.nodes(edge=["CONTAINS"], direction="out", node=["Track"])
    return [
        t
        for t in tracks
        if isinstance(t, Track)
        and str(getattr(t, "template_id", "") or "") == PROJECT_DETAILS_TEMPLATE
    ]


async def _sprints_tracks(app: App) -> List[Track]:
    tracks = await app.nodes(edge=["CONTAINS"], direction="out", node=["Track"])
    out: List[Track] = []
    for t in tracks:
        if not isinstance(t, Track):
            continue
        title = str(getattr(t, "title", "") or "").strip()
        template_id = str(getattr(t, "template_id", "") or "").strip()
        if title == SPRINTS_TRACK_TITLE or template_id == SPRINTS_TRACK_TYPE_KEY:
            out.append(t)
    return out


async def _materialize_anchor_entry_types(track: Track) -> None:
    from app.services.content_profile_runtime import (
        ensure_track_views_materialized_from_tier,
    )
    from app.services.entry_type_service import materialize_entry_types_from_tier

    await ensure_track_views_materialized_from_tier(track)
    await materialize_entry_types_from_tier(track)


async def _backfill_task_buckets(
    track: Track,
    *,
    dry_run: bool,
    stats: Dict[str, int],
) -> None:
    owner_id = str(getattr(track, "owner_id", "") or "").strip()
    assignee_pool = [owner_id] if owner_id else []
    entries = await track.nodes(edge=["CONTAINS"], direction="out", node=["Entry"])
    for ent in entries:
        if not isinstance(ent, Entry):
            continue
        cf = dict(getattr(ent, "custom_fields", None) or {})
        title_key = str(getattr(ent, "title", "") or "").strip().casefold()
        dirty = False
        if not str(cf.get("bucket") or "").strip():
            status = str(cf.get("status") or "").strip()
            bucket = TITLE_TO_BUCKET.get(title_key) or STATUS_TO_BUCKET.get(status)
            if bucket:
                cf["bucket"] = bucket
                stats["tasks_bucket_patched"] += 1
                dirty = True
            else:
                stats["tasks_bucket_skipped"] += 1
        else:
            stats["tasks_bucket_already_set"] += 1
        if not str(cf.get("assignee") or "").strip() and assignee_pool:
            slot = TITLE_TO_ASSIGNEE_SLOT.get(title_key, 0)
            cf["assignee"] = assignee_pool[slot % len(assignee_pool)]
            stats["tasks_assignee_patched"] = stats.get("tasks_assignee_patched", 0) + 1
            dirty = True
        if not dirty:
            continue
        if dry_run:
            logger.info(
                "[dry] track %s entry %r → custom_fields %s",
                track.id,
                ent.title,
                cf,
            )
            continue
        ent.custom_fields = cf
        await ent.save()


async def _upgrade_app(app: App, *, dry_run: bool, stats: Dict[str, int]) -> None:
    stats["apps_scanned"] += 1
    owner_id = str(getattr(app, "owner_id", "") or "").strip()
    if not owner_id:
        logger.warning("App %s has no owner_id; skipping library update", app.id)
    elif dry_run:
        logger.info("[dry] would update App %s from library", app.id)
    else:
        lib_id = str(getattr(app, "installed_from_library_id", "") or "").strip()
        if lib_id:
            await update_app_from_library(app_id=app.id, actor_id=owner_id)
            stats["apps_updated_from_library"] += 1
        else:
            app_cp = await get_app_attached_content_profile(app)
            if app_cp is not None:
                refreshed = await refresh_all_app_track_template_materializations(app)
                stats["template_cps_refreshed"] += refreshed

    anchor_tracks = await _project_details_anchor_tracks(app)
    stats["anchor_tracks_found"] += len(anchor_tracks)
    for track in anchor_tracks:
        if dry_run:
            logger.info(
                "[dry] would upgrade anchor track %s (%s)",
                track.id,
                track.title,
            )
        else:
            await _materialize_anchor_entry_types(track)
            await synchronize_track_view_default_flags(track)
            stats["anchor_tracks_upgraded"] += 1
        await _backfill_task_buckets(track, dry_run=dry_run, stats=stats)

    sprints_tracks = await _sprints_tracks(app)
    stats["sprints_tracks_found"] += len(sprints_tracks)
    for track in sprints_tracks:
        if dry_run:
            logger.info(
                "[dry] would materialize Sprints track %s (%s)",
                track.id,
                track.title,
            )
            continue
        await _materialize_anchor_entry_types(track)
        await synchronize_track_view_default_flags(track)
        stats["sprints_tracks_upgraded"] += 1
    if not sprints_tracks and not dry_run:
        logger.warning(
            "App %s has no Sprints track after library update — create via "
            "UI or re-run seed (ensure_projects_sprints_ready)",
            app.id,
        )


async def run(*, dry_run: bool) -> Dict[str, int]:
    stats: Dict[str, int] = {
        "apps_scanned": 0,
        "apps_updated_from_library": 0,
        "template_cps_refreshed": 0,
        "anchor_tracks_found": 0,
        "anchor_tracks_upgraded": 0,
        "sprints_tracks_found": 0,
        "sprints_tracks_upgraded": 0,
        "tasks_bucket_patched": 0,
        "tasks_bucket_already_set": 0,
        "tasks_bucket_skipped": 0,
        "tasks_assignee_patched": 0,
    }
    if not dry_run:
        from app.services.app_graph import ensure_library_catalog_seeded

        sync_stats = await ensure_library_catalog_seeded()
        logger.info(
            "Library catalog sync: added=%s updated=%s removed=%s issues=%s",
            len(sync_stats.get("added") or []),
            len(sync_stats.get("updated") or []),
            len(sync_stats.get("removed") or []),
            len(sync_stats.get("issues") or []),
        )

    apps = await _find_projects_apps()
    if not apps:
        logger.info("No Projects App found; nothing to upgrade")
        return stats
    for app in apps:
        await _upgrade_app(app, dry_run=dry_run, stats=stats)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Upgrade Projects anchor tracks to Planner bucket kanban",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log planned actions without writing",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from app.services.db_init import init_prime_db

    init_prime_db()
    stats = asyncio.run(run(dry_run=bool(args.dry_run)))
    logger.info("Upgrade complete: %s", stats)
    print("upgrade_project_details_planner stats:")
    for k, v in stats.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
