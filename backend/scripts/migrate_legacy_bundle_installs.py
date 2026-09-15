"""One-time migration: heal legacy library installs via install_app.

For each workspace, finds Apps with ``installed_from_library_id`` set but
``lifecycle_state`` not ``active`` or missing ``version``, and re-runs
``install_app`` idempotently to complete the lifecycle transaction.
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from app.models.nodes import App, Workspace
from app.services.app_lifecycle import install_app

logger = logging.getLogger(__name__)


async def migrate_legacy_bundle_installs(*, dry_run: bool = False) -> dict:
    stats = {"workspaces": 0, "candidates": 0, "reinstalled": 0, "skipped": 0}
    workspaces = await Workspace.find()
    stats["workspaces"] = len(workspaces)

    for ws in workspaces:
        apps = await App.find({"context.workspace_id": ws.id})
        for app in apps or []:
            lib_id = str(getattr(app, "installed_from_library_id", "") or "").strip()
            if not lib_id:
                continue
            lifecycle = str(getattr(app, "lifecycle_state", "") or "")
            version = str(getattr(app, "version", "") or "").strip()
            needs_heal = lifecycle != "active" or not version
            if not needs_heal:
                stats["skipped"] += 1
                continue
            stats["candidates"] += 1
            owner = str(getattr(app, "owner_user_id", "") or "").strip()
            if not owner:
                logger.warning(
                    "skip app %s — no owner_user_id (workspace=%s)",
                    app.id,
                    ws.id,
                )
                stats["skipped"] += 1
                continue
            if dry_run:
                logger.info(
                    "[dry] would reinstall app %s (lifecycle=%s version=%r lib=%s)",
                    app.id,
                    lifecycle,
                    version,
                    lib_id,
                )
                stats["reinstalled"] += 1
                continue
            try:
                await install_app(
                    workspace_id=ws.id,
                    library_cp_id=lib_id,
                    actor_id=owner,
                )
                stats["reinstalled"] += 1
                logger.info(
                    "reinstalled legacy bundle app %s in workspace %s",
                    app.id,
                    ws.id,
                )
            except Exception:
                logger.exception(
                    "install_app failed for legacy app %s (workspace=%s)",
                    app.id,
                    ws.id,
                )
                stats["skipped"] += 1
    return stats


async def _main(dry_run: bool) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from app.services.db_init import init_prime_db

    init_prime_db()
    stats = await migrate_legacy_bundle_installs(dry_run=dry_run)
    prefix = "[dry-run] " if dry_run else ""
    print(f"{prefix}migrate_legacy_bundle_installs => {stats}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true")
    args = parser.parse_args()
    asyncio.run(_main(dry_run=args.dry))
