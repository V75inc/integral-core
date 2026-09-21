"""Phase 16 ACC-05 — Seed representative Task entries.

For every Project task track (auto-provisioned via the project-details
anchor template) in every Projects App, plus any ``Engineering Standup``
top-level track, create 4-8 representative Task entries spread across the
four `status` values so the kanban board is non-empty.

Idempotent: re-runs detect existing seeded tasks via the
``Entry.context.seeded_by`` stamp and skip.

Usage::

    # Dry run.
    python -m scripts.seed_default_tasks --dry-run

    # Live run.
    python -m scripts.seed_default_tasks

No-ops gracefully against an empty DB. Each created Entry wires
Track —CONTAINS→ Entry in the same function as create (I-GRAPH-01).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List

from app.models.edges import CONTAINS
from app.models.nodes import App, Entry, Track

logger = logging.getLogger(__name__)

PROJECTS_APP_SLUG = "projects"
STANDUP_TRACK_NAME = "engineering standup"
SEED_TAG = "default_seed_tasks"

# Representative seed shape. Each project task track gets the same set so
# every kanban board exhibits the four-column flow.
TASK_SEEDS: List[Dict[str, object]] = [
    {
        "title": "Spec API contract",
        "bucket": "in_progress",
        "status": "in_review",
        "priority": "high",
        "estimate": 3,
        "due_offset_days": 2,
        "start_offset_days": -1,
        "assignee_slot": 0,
    },
    {
        "title": "Wire frontend route",
        "bucket": "this_week",
        "status": "in_progress",
        "priority": "medium",
        "estimate": 5,
        "due_offset_days": 5,
        "start_offset_days": 0,
        "assignee_slot": 1,
    },
    {
        "title": "Draft regression tests",
        "bucket": "backlog",
        "status": "todo",
        "priority": "medium",
        "estimate": 4,
        "due_offset_days": 7,
        "start_offset_days": 2,
        "assignee_slot": 2,
    },
    {
        "title": "Schema migration script",
        "bucket": "backlog",
        "status": "todo",
        "priority": "high",
        "estimate": 6,
        "due_offset_days": 10,
        "start_offset_days": 3,
        "assignee_slot": 0,
    },
    {
        "title": "Document the change in the runbook",
        "bucket": "done",
        "status": "done",
        "priority": "low",
        "estimate": 1,
        "due_offset_days": -3,
        "start_offset_days": -5,
        "assignee_slot": 1,
    },
    {
        "title": "Verify with stakeholders",
        "bucket": "this_week",
        "status": "in_review",
        "priority": "medium",
        "estimate": 2,
        "due_offset_days": 3,
        "start_offset_days": -2,
        "assignee_slot": 2,
    },
]


async def _find_projects_apps() -> List[App]:
    apps = await App.find()
    out: List[App] = []
    for app in apps:
        slug = getattr(app, "attached_operational_model_slug", "") or ""
        if slug == PROJECTS_APP_SLUG:
            out.append(app)
    return out


async def _project_task_tracks(app: App) -> List[Track]:
    """Return every track auto-provisioned under the project-details template.

    The project-details template is the anchor target for the
    ``details_track`` relation on each Project entry. Each Project ends
    up anchoring a sibling Track under the App; the track's
    ``template_id`` is ``project-details``.
    """
    tracks = await app.nodes(edge=["CONTAINS"], direction="out", node=["Track"])
    return [
        t
        for t in tracks
        if isinstance(t, Track)
        and (getattr(t, "template_id", "") or "") == "project-details"
    ]


async def _find_standup_tracks(app: App) -> List[Track]:
    """Return any top-level track named like the Engineering Standup."""
    tracks = await app.nodes(edge=["CONTAINS"], direction="out", node=["Track"])
    out: List[Track] = []
    for t in tracks:
        if not isinstance(t, Track):
            continue
        name = (getattr(t, "title", "") or "").strip().lower()
        if STANDUP_TRACK_NAME in name:
            out.append(t)
    return out


async def _track_has_seeded_tasks(track: Track) -> bool:
    entries = await track.nodes(edge=["CONTAINS"], direction="out", node=["Entry"])
    for e in entries:
        if not isinstance(e, Entry):
            continue
        ctx = getattr(e, "context", {}) or {}
        if str(ctx.get("seeded_by") or "") == SEED_TAG:
            return True
        cf = getattr(e, "custom_fields", {}) or {}
        if str(cf.get("__anchor_seed_tag") or "") == SEED_TAG:
            return True
    return False


async def _seed_track(
    *,
    track: Track,
    dry_run: bool,
    stats: Dict[str, int],
) -> None:
    if await _track_has_seeded_tasks(track):
        stats["tracks_already_seeded"] += 1
        return
    now = datetime.now(timezone.utc)
    owner_id = getattr(track, "owner_id", "") or "default-seed-tasks"
    assignee_ids = [owner_id]
    for idx, seed in enumerate(TASK_SEEDS):
        due_dt = now + timedelta(days=int(seed["due_offset_days"]))
        custom_fields = {
            "bucket": seed["bucket"],
            "status": seed["status"],
            "priority": seed["priority"],
            "estimate": seed["estimate"],
            "due_date": due_dt.date().isoformat(),
            "start_date": (now + timedelta(days=int(seed.get("start_offset_days", 0))))
            .date()
            .isoformat(),
            "assignee": assignee_ids[
                int(seed.get("assignee_slot", idx)) % len(assignee_ids)
            ],
        }
        title = str(seed["title"])
        if dry_run:
            logger.info(
                "[dry] would seed task %r on track %s (%s/%s)",
                title,
                track.id,
                seed["status"],
                seed["priority"],
            )
            stats["tasks_would_seed"] += 1
            continue
        new_entry = await Entry.create(
            title=title,
            body="",
            track_id=track.id,
            author_id=owner_id,
            custom_fields=custom_fields,
            context={"seeded_by": SEED_TAG},
        )
        # I-GRAPH-01 — wire Track —CONTAINS→ Entry in the same function.
        await track.connect(
            new_entry,
            edge=CONTAINS,
            added_at=now.isoformat(),
        )
        stats["tasks_seeded"] += 1
    stats["tracks_seeded"] += 1


async def seed(*, dry_run: bool = False) -> Dict[str, int]:
    stats: Dict[str, int] = {
        "projects_apps": 0,
        "project_task_tracks": 0,
        "standup_tracks": 0,
        "tracks_seeded": 0,
        "tracks_already_seeded": 0,
        "tasks_seeded": 0,
        "tasks_would_seed": 0,
    }
    apps = await _find_projects_apps()
    stats["projects_apps"] = len(apps)
    if not apps:
        logger.info("seed_default_tasks: no Projects Apps found; no-op")
        return stats
    for app in apps:
        proj_tracks = await _project_task_tracks(app)
        stats["project_task_tracks"] += len(proj_tracks)
        for t in proj_tracks:
            await _seed_track(track=t, dry_run=dry_run, stats=stats)
        standup_tracks = await _find_standup_tracks(app)
        stats["standup_tracks"] += len(standup_tracks)
        for t in standup_tracks:
            await _seed_track(track=t, dry_run=dry_run, stats=stats)
    return stats


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    from app.services.db_init import init_prime_db

    init_prime_db()
    stats = asyncio.run(seed(dry_run=args.dry_run))
    print("seed_default_tasks stats:")
    for k, v in stats.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
