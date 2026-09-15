"""One-off migration: re-run ``update_app_from_library`` on every already-
installed Guyana Payroll (``payroll-app``) App so its tracks pick up the
"Guyana "-prefix rename (see content_profile_merge.py's track-title-sync
fix). Idempotent — re-running is a no-op for any App already renamed.

Usage::

    python -m scripts.migrate_guyana_payroll_track_titles [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from app.models.nodes import App
from app.services.app_graph import get_app_attached_content_profile
from app.services.app_lifecycle import update_app_from_library

logger = logging.getLogger(__name__)


async def _find_installed_payroll_app_apps() -> list[App]:
    """Match the App's CURRENTLY ATTACHED ContentProfile's
    ``package.name`` — that field is ALWAYS the slug by design
    (content_profile_loader._assemble_manifest convention), not
    ``package.slug`` (not populated on this shape). Matching the
    attached CP (not ``installed_from_library_id``'s referent) is also
    what actually determines behavior today, regardless of what library
    row the App originally installed from."""
    out: list[App] = []
    for app_node in await App.find({}):
        if getattr(app_node, "lifecycle_state", "") == "uninstalled":
            continue
        cp = await get_app_attached_content_profile(app_node)
        if not cp:
            continue
        name = ((cp.manifest or {}).get("package") or {}).get("name")
        if name == "payroll-app":
            out.append(app_node)
    return out


async def _resolve_actor_id(app_node: App) -> str:
    owners = await app_node.nodes(edge=["OWNS"], node=["User"], direction="in")
    if owners:
        return owners[0].id
    return "system"


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from app.services.db_init import init_prime_db

    init_prime_db()

    # Wizard-step / view-type / field-type plugin registries (region_system,
    # payroll_register, etc.) are only populated when something calls this —
    # normal app startup does it in app/main.py; a standalone script must do
    # it itself or compile_canonical_manifest fails on any entry type using
    # a plugin-registered create_wizard step kind.
    from app.services.content_profile_plugins import discover_and_register_plugins

    discover_and_register_plugins()

    apps = await _find_installed_payroll_app_apps()
    logger.info("Found %d installed Guyana Payroll app(s)", len(apps))
    for app_node in apps:
        actor_id = await _resolve_actor_id(app_node)
        logger.info(
            "App %s (workspace %s, actor %s)%s",
            app_node.id,
            app_node.workspace_id,
            actor_id,
            " [dry-run, skipping]" if args.dry_run else "",
        )
        if args.dry_run:
            continue
        result = await update_app_from_library(app_id=app_node.id, actor_id=actor_id)
        logger.info("  -> %s", result)


if __name__ == "__main__":
    asyncio.run(main())
