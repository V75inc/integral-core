"""Phase 16 — Migrate cost / contract data into anchored privileged tracks.

Idempotent one-shot script. For every Project entry in every Workspace
that has the standalone ``projects`` App attached (Phase 31 post-split):

1. Ensure the ``Project Financials`` and ``Contracts & Legal`` anchored
   tracks exist (auto-provision via ``materialize_anchor_track`` when
   missing) and the source Project entry → privileged-track ANCHORS edges
   are wired (via the sanctioned single-writer ``_sync_anchor_edges``).
2. If the legacy Project entry carries a ``budget`` field value (the only
   finance-shaped field on the pre-ACC-02 Project EntryType in
   ``app/profiles/projects/profile.yaml``), copy it into a new
   ``project_financials`` entry on the anchored Project Financials track
   as ``cost``. Idempotent via ``Entry.context.migrated_from_project_id``.
3. No data to migrate for Contracts & Legal — the legacy Project EntryType
   declares no contract-shaped fields. The empty anchored Contracts & Legal
   track stands ready for operators to populate manually.

The script is safe to run against an empty / unprovisioned DB — it no-ops
when no ``projects`` App is found.

Usage::

    # Dry run — prints planned actions without writing.
    python -m scripts.migrate_privileged_fields --dry-run

    # Live run — idempotent.
    python -m scripts.migrate_privileged_fields

Phase 16 ACC-02 implementation. Invariant:
``docs/INVARIANTS.md`` § I-ACCESS-01 (privacy-via-modeling, not
field-level visibility).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from app.models.edges import CONTAINS
from app.models.nodes import App, Entry, Track
from app.services.content_profile_graph import (
    _maybe_reuse_existing_anchor,
    _sync_anchor_edges,
    materialize_anchor_track,
)

logger = logging.getLogger(__name__)

# Source manifest declares these anchor relation fields on the Project EntryType
# (see backend/app/profiles/projects/profile.yaml).
PRIVILEGED_ANCHORS: List[Dict[str, str]] = [
    {"field_key": "financials_track", "template_key": "project-financials"},
    {"field_key": "contracts_track", "template_key": "contracts-legal"},
]

PROJECTS_APP_SLUG = "projects"
PROJECTS_TRACK_KEY = "projects"
PROJECT_ENTRY_TYPE_KEY = "project"


async def _find_projects_apps() -> List[App]:
    """Return every App whose attached package slug is the Projects library."""
    apps = await App.find()
    out: List[App] = []
    for app in apps:
        slug = getattr(app, "attached_content_profile_slug", "") or ""
        if slug == PROJECTS_APP_SLUG:
            out.append(app)
    return out


async def _find_projects_track(app: App) -> Optional[Track]:
    """Return the App's projects track if it carries the canonical key."""
    tracks = await app.nodes(edge=["CONTAINS"], direction="out", node=["Track"])
    for t in tracks:
        if not isinstance(t, Track):
            continue
        # Track template key match — accept either canonical key or fallback by name.
        if (getattr(t, "track_template_key", "") or "") == PROJECTS_TRACK_KEY:
            return t
        if (getattr(t, "title", "") or "").strip().lower() == "projects":
            return t
    return None


async def _project_entries(track: Track) -> List[Entry]:
    """Return all Project entries on the projects track."""
    entries = await track.nodes(edge=["CONTAINS"], direction="out", node=["Entry"])
    return [e for e in entries if isinstance(e, Entry)]


async def _ensure_anchor(
    *,
    source_entry: Entry,
    source_track: Track,
    field_key: str,
    template_key: str,
    dry_run: bool,
) -> Optional[Track]:
    """Provision the privileged anchored track if missing; wire the ANCHORS edge."""
    existing_id = await _maybe_reuse_existing_anchor(
        source_entry=source_entry, field_key=field_key
    )
    if existing_id is not None:
        anchored = await Track.get(existing_id)
        return anchored
    if dry_run:
        logger.info(
            "[dry] would provision anchor track for entry %s field %s template %s",
            source_entry.id,
            field_key,
            template_key,
        )
        return None
    anchored = await materialize_anchor_track(
        source_track=source_track,
        template_key=template_key,
        field_key=field_key,
        actor_user_id="privileged-fields-migration",
        actor_kind="system",
        source_entry_title=(getattr(source_entry, "title", "") or "Project"),
    )
    # Single-writer ANCHORS edge wire — _sync_anchor_edges is the sole sanctioned
    # write path (INVARIANTS.md L101-116). Script invokes it; it owns the edge.
    await _sync_anchor_edges(
        source_entry=source_entry,
        relation_refs=[
            {
                "field_key": field_key,
                "targets": [anchored.id],
                "target": "track",
            }
        ],
    )
    logger.info(
        "provisioned anchor track %s for entry %s field %s",
        anchored.id,
        source_entry.id,
        field_key,
    )
    return anchored


async def _find_existing_financials_entry(
    *,
    financials_track: Track,
    source_project_id: str,
) -> Optional[Entry]:
    """Idempotency: locate a previously-migrated financials entry for this project."""
    entries = await financials_track.nodes(
        edge=["CONTAINS"], direction="out", node=["Entry"]
    )
    for e in entries:
        if not isinstance(e, Entry):
            continue
        ctx = getattr(e, "context", {}) or {}
        if str(ctx.get("migrated_from_project_id") or "") == source_project_id:
            return e
    return None


async def _migrate_budget_to_financials(
    *,
    project: Entry,
    financials_track: Track,
    dry_run: bool,
    stats: Dict[str, int],
) -> None:
    """Copy legacy project.budget into a project_financials entry. Idempotent."""
    custom = getattr(project, "custom_fields", {}) or {}
    budget = custom.get("budget")
    if budget in (None, "", 0):
        stats["no_budget"] += 1
        return
    existing = await _find_existing_financials_entry(
        financials_track=financials_track,
        source_project_id=project.id,
    )
    if existing is not None:
        stats["already_migrated"] += 1
        return
    title = f"{getattr(project, 'title', 'Project')} — Financials"
    if dry_run:
        logger.info(
            "[dry] would create project_financials entry on track %s for project %s "
            "(cost=%r)",
            financials_track.id,
            project.id,
            budget,
        )
        stats["would_migrate"] += 1
        return
    now = datetime.now(timezone.utc).isoformat()
    new_entry = await Entry.create(
        title=title,
        body="",
        track_id=financials_track.id,
        owner_id=(getattr(project, "owner_id", "") or "privileged-fields-migration"),
        custom_fields={"cost": budget},
        context={"migrated_from_project_id": project.id},
    )
    # I-GRAPH-01 — wire Track —CONTAINS→ Entry in the same function as the create.
    await financials_track.connect(new_entry, edge=CONTAINS, added_at=now)
    stats["migrated"] += 1
    logger.info(
        "migrated project %s budget=%r -> financials entry %s",
        project.id,
        budget,
        new_entry.id,
    )


async def migrate(*, dry_run: bool = False) -> Dict[str, int]:
    stats: Dict[str, int] = {
        "projects_apps": 0,
        "projects_tracks": 0,
        "projects": 0,
        "anchors_provisioned": 0,
        "anchors_already_present": 0,
        "no_budget": 0,
        "already_migrated": 0,
        "would_migrate": 0,
        "migrated": 0,
        "errors": 0,
    }
    apps = await _find_projects_apps()
    stats["projects_apps"] = len(apps)
    if not apps:
        logger.info("migrate_privileged_fields: no Projects Apps found; no-op")
        return stats
    for app in apps:
        projects_track = await _find_projects_track(app)
        if projects_track is None:
            logger.warning(
                "migrate_privileged_fields: App %s has no projects track; skipping",
                app.id,
            )
            continue
        stats["projects_tracks"] += 1
        entries = await _project_entries(projects_track)
        stats["projects"] += len(entries)
        for project in entries:
            try:
                anchors: Dict[str, Optional[Track]] = {}
                for anchor in PRIVILEGED_ANCHORS:
                    pre = await _maybe_reuse_existing_anchor(
                        source_entry=project, field_key=anchor["field_key"]
                    )
                    if pre is not None:
                        stats["anchors_already_present"] += 1
                        anchors[anchor["field_key"]] = await Track.get(pre)
                        continue
                    anchored = await _ensure_anchor(
                        source_entry=project,
                        source_track=projects_track,
                        field_key=anchor["field_key"],
                        template_key=anchor["template_key"],
                        dry_run=dry_run,
                    )
                    if anchored is not None:
                        stats["anchors_provisioned"] += 1
                    anchors[anchor["field_key"]] = anchored
                fin_track = anchors.get("financials_track")
                if fin_track is not None:
                    await _migrate_budget_to_financials(
                        project=project,
                        financials_track=fin_track,
                        dry_run=dry_run,
                        stats=stats,
                    )
            except Exception as exc:
                logger.exception(
                    "migrate_privileged_fields: project %s failed: %s",
                    project.id,
                    exc,
                )
                stats["errors"] += 1
    return stats


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan migration without writing.",
    )
    args = parser.parse_args()
    from app.services.db_init import init_prime_db

    init_prime_db()
    stats = asyncio.run(migrate(dry_run=args.dry_run))
    print("migrate_privileged_fields stats:")
    for k, v in stats.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
