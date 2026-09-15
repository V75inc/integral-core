"""Phase 16 — Author the default policy set + role/exclusion pass (ACC-03 + ACC-04).

Idempotent one-shot script. Two operations:

1. **Authored Policy records** (ACC-03) — declarative intent visible in
   Settings → Policies (I-SET-01). The policies record engineer-role
   default-deny on `project-financials`, `contracts-legal`, and
   forward-declared `payroll-*` track templates. The actual enforcement
   for individual users is the `EXCLUDED_FROM` pass in step 2.

2. **Role + exclusion pass** (ACC-04) — consumes a roster YAML file
   declaring each collaborator's email, workspace role, CRM App role,
   and per-resource exclusions. Applies `OWNS` / `COLLABORATES_ON` for
   grants and `EXCLUDED_FROM` for explicit deny via
   `app.services.sharing.add_exclusion` (the canonical write path).

Usage::

    # Dry run — print planned actions.
    python -m scripts.author_default_policies --dry-run

    # Live run — idempotent.
    python -m scripts.author_default_policies

    # Use a custom roster path.
    python -m scripts.author_default_policies --roster path/to/roster.yaml

The script is safe to run against an empty / unprovisioned DB — it no-ops
when no target workspace or roster collaborators are found.

Companion documents:
- `docs/INVARIANTS.md` § I-ACCESS-01 (privacy-via-modeling — anchored
  privileged tracks + EXCLUDED_FROM, the pattern this script materializes)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from app.models.nodes import App, Policy, Track, User, Workspace
from app.services.policy_registry import create_policy

logger = logging.getLogger(__name__)

# ACC-03 — declarative policy intent. Each row materializes a Policy node
# with subject_kind="human", subject_id="engineer" (the functional role,
# stored verbatim — the engine's default-human path consults role-ladder
# enforcement separately; this Policy is the settings-visible record of the
# deny intent).
PRIVILEGED_DENY_POLICIES: List[Dict[str, Any]] = [
    {
        "subject_kind": "human",
        "subject_id": "engineer",
        "scope": "track_template:project-financials",
        "actions": [],  # empty actions = default-deny (engine fails closed)
        "is_active": True,
        "_intent": "engineer-role default-deny on Project Financials tracks (ACC-03)",
    },
    {
        "subject_kind": "human",
        "subject_id": "engineer",
        "scope": "track_template:contracts-legal",
        "actions": [],
        "is_active": True,
        "_intent": "engineer-role default-deny on Contracts & Legal tracks (ACC-03)",
    },
    {
        "subject_kind": "human",
        "subject_id": "engineer",
        "scope": "track_template:payroll-*",
        "actions": [],
        "is_active": True,
        "_intent": (
            "engineer-role forward-declared default-deny on Payroll tracks "
            "(ACC-03; Phase 17 attaches the actual Payroll tracks)"
        ),
    },
]


async def _find_existing_policy(scope: str, subject_id: str) -> Optional[Policy]:
    """Lookup by (subject_kind=human, subject_id, scope) for idempotency."""
    rows = await Policy.find(
        {
            "context.subject_kind": "human",
            "context.subject_id": subject_id,
            "context.scope": scope,
        }
    )
    return rows[0] if rows else None


async def author_policies(*, dry_run: bool, created_by: str) -> Dict[str, int]:
    stats = {"existing": 0, "created": 0, "would_create": 0}
    for spec in PRIVILEGED_DENY_POLICIES:
        scope = spec["scope"]
        subject_id = spec["subject_id"]
        existing = await _find_existing_policy(scope=scope, subject_id=subject_id)
        if existing is not None:
            stats["existing"] += 1
            logger.info("policy already present: %s (%s)", scope, spec["_intent"])
            continue
        if dry_run:
            logger.info("[dry] would create policy: %s (%s)", scope, spec["_intent"])
            stats["would_create"] += 1
            continue
        await create_policy(
            subject_kind=spec["subject_kind"],
            subject_id=subject_id,
            scope=scope,
            actions=list(spec.get("actions") or []),
            is_active=bool(spec.get("is_active", True)),
            created_by=created_by,
        )
        stats["created"] += 1
        logger.info("policy created: %s (%s)", scope, spec["_intent"])
    return stats


async def _resolve_workspace(workspace_id_or_auto: str) -> Optional[Workspace]:
    if workspace_id_or_auto and workspace_id_or_auto != "auto":
        return await Workspace.get(workspace_id_or_auto)
    # "auto" — find the configured seed workspace by name substring.
    # Driven by INTEGRAL_SEED_WORKSPACE_NAME so the auto-detect heuristic
    # has no customer-specific brand baked in.
    needle = (os.environ.get("INTEGRAL_SEED_WORKSPACE_NAME") or "").strip().lower()
    if not needle:
        return None
    all_ws = await Workspace.find()
    for ws in all_ws:
        if needle in (getattr(ws, "name", "") or "").lower():
            return ws
    return None


async def _find_user_by_email(email: str) -> Optional[User]:
    rows = await User.find({"context.email": email})
    return rows[0] if rows else None


async def _find_app_by_slug(workspace: Workspace, slug: str) -> Optional[App]:
    apps = await workspace.nodes(
        edge=["CONTAINS"], direction="out", node=["WorkspaceApp"]
    )
    for app in apps:
        if not isinstance(app, App):
            continue
        if (getattr(app, "attached_content_profile_slug", "") or "") == slug:
            return app
    return None


async def _find_track_by_template_key(app: App, template_key: str) -> Optional[Track]:
    tracks = await app.nodes(edge=["CONTAINS"], direction="out", node=["Track"])
    for t in tracks:
        if not isinstance(t, Track):
            continue
        if (getattr(t, "track_template_key", "") or "") == template_key:
            return t
    return None


async def apply_roster(
    *,
    roster: Dict[str, Any],
    dry_run: bool,
    actor_user_id: str,
) -> Dict[str, int]:
    """Apply collaborator + exclusion grants per the roster YAML.

    Best-effort: missing User accounts are warned + skipped (roster file
    may declare emails before SSO has provisioned the corresponding User
    nodes).
    """
    stats = {
        "collaborators_processed": 0,
        "users_missing": 0,
        "exclusions_applied": 0,
        "exclusions_already_present": 0,
        "exclusions_would_apply": 0,
        "exclusions_skipped_no_track": 0,
    }
    workspace_id = str(roster.get("workspace_id") or "auto")
    workspace = await _resolve_workspace(workspace_id)
    if workspace is None:
        logger.info(
            "author_default_policies: no target workspace resolved (id=%r); "
            "roster pass no-op",
            workspace_id,
        )
        return stats

    projects_app_slug = str(
        roster.get("projects_app_slug") or roster.get("crm_app_slug") or "projects"
    )
    projects_app = await _find_app_by_slug(workspace, projects_app_slug)
    if projects_app is None:
        logger.warning(
            "author_default_policies: workspace %s has no %s App; "
            "exclusion pass will run only against globally findable tracks",
            workspace.id,
            projects_app_slug,
        )

    from app.services.sharing import add_exclusion  # local import — heavy module

    for collab in roster.get("collaborators") or []:
        stats["collaborators_processed"] += 1
        email = str(collab.get("email") or "").strip()
        if not email:
            continue
        user = await _find_user_by_email(email)
        if user is None:
            stats["users_missing"] += 1
            logger.warning(
                "author_default_policies: roster user %r not provisioned; skipping",
                email,
            )
            continue

        # Workspace + App role grants are skipped here because the canonical
        # write surface for those is the share/collaborator API which gates
        # on share-authority + emits ChangeEvents. Running the grant pass in
        # this script would duplicate the existing UI flow without adding
        # value. EXCLUDED_FROM is the script's load-bearing operation —
        # it's the explicit-deny edge ACC-04 demands.

        exclusions = collab.get("excluded_from") or []
        for exc in exclusions:
            track_key = str(exc.get("track_template_key") or "").strip()
            if not track_key or projects_app is None:
                stats["exclusions_skipped_no_track"] += 1
                continue
            # Resolve forward-declared payroll-* glob to no-op (Phase 17 attaches).
            if track_key.endswith("-*"):
                logger.info(
                    "exclusion: skipping glob %r for user %s — Phase 17 attaches",
                    track_key,
                    email,
                )
                stats["exclusions_skipped_no_track"] += 1
                continue
            track = await _find_track_by_template_key(projects_app, track_key)
            if track is None:
                logger.warning(
                    "exclusion: no track with template_key=%r in Projects App; "
                    "user %s not yet excluded (re-run after anchor "
                    "auto-provision fires for this Project)",
                    track_key,
                    email,
                )
                stats["exclusions_skipped_no_track"] += 1
                continue
            # add_exclusion is idempotent via ensure_edge (services/edge_upsert.py).
            if dry_run:
                logger.info(
                    "[dry] would add EXCLUDED_FROM: user=%s track=%s (template=%s)",
                    user.id,
                    track.id,
                    track_key,
                )
                stats["exclusions_would_apply"] += 1
                continue
            try:
                await add_exclusion(
                    actor_user_id=actor_user_id,
                    resource_type="track",
                    resource_id=track.id,
                    user_id_to_exclude=user.id,
                    reason=f"ACC-04 — engineer default-deny on {track_key}",
                )
                stats["exclusions_applied"] += 1
            except Exception as exc:
                logger.warning(
                    "exclusion: add_exclusion failed for user %s track %s: %s",
                    user.id,
                    track.id,
                    exc,
                )
    return stats


async def run(
    *,
    dry_run: bool,
    roster_path: Optional[Path],
    actor_user_id: str,
) -> Dict[str, Any]:
    policy_stats = await author_policies(dry_run=dry_run, created_by=actor_user_id)
    roster: Dict[str, Any] = {}
    if roster_path is not None and roster_path.is_file():
        roster = yaml.safe_load(roster_path.read_text()) or {}
    else:
        logger.info(
            "author_default_policies: roster file %r not found; skipping role pass",
            str(roster_path) if roster_path else None,
        )
    roster_stats = await apply_roster(
        roster=roster, dry_run=dry_run, actor_user_id=actor_user_id
    )
    return {"policies": policy_stats, "roster": roster_stats}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Plan without writing.")
    parser.add_argument(
        "--roster",
        default="backend/scripts/default_roster.yaml",
        help="Path to the roster YAML (default: backend/scripts/default_roster.yaml).",
    )
    parser.add_argument(
        "--actor",
        default="author-default-policies-script",
        help="actor_user_id stamped on emitted ChangeEvents (default: script id).",
    )
    args = parser.parse_args()
    roster_path = Path(args.roster) if args.roster else None
    from app.services.db_init import init_prime_db

    init_prime_db()
    out = asyncio.run(
        run(
            dry_run=args.dry_run,
            roster_path=roster_path,
            actor_user_id=args.actor,
        )
    )
    print("author_default_policies stats:")
    for section, stats in out.items():
        print(f"  {section}:")
        for k, v in stats.items():
            print(f"    {k}: {v}")


if __name__ == "__main__":
    main()
