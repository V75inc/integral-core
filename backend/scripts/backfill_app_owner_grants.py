#!/usr/bin/env python3
"""Repair Apps that no principal can administer.

An App with no ``OWNS`` edge is a dead end. Track creation, content-profile
mutation and collaborator management all gate on ``app.update`` →
``can_admin_app`` → ``resolve_role(...) in ("owner", "admin")``, and an org
workspace owner resolves only to the implicit staff ``viewer``
(``workspace_staff_implicit_resource_role`` — inventory visibility by design).
So when the grant is missing, *every* human gets 403 and no UI surface can put
it back.

Apps reached that state through three silent skips, all fixed forward in
``wire_app_owner`` / ``install_app`` / ``create_app_internal``. This script
repairs the rows those skips already produced.

Two conditions are repaired:

1. **No ``OWNS`` edge at all.** The grant goes to the App's recorded
   ``owner_user_id`` when that resolves to a graph profile, otherwise to the
   workspace owner — the principal who would otherwise be locked out of an App
   in their own workspace.
2. **``owner_user_id`` holding a non-graph id** — only with
   ``--normalize-owner-ids``. Every reader funnels this field through
   ``get_user_node``, which accepts either form, so a principal id here is a
   consistency wart rather than a live fault; ``ownership_transfer`` already
   writes the graph id. It is opt-in because the repair rewrites rows that are
   not actually broken.

Uninstalled Apps are skipped — they are not reachable and reinstalling wires a
fresh grant.

Usage (from ``backend/``, with the same ``JVSPATIAL_*`` env the API uses)::

    python scripts/backfill_app_owner_grants.py --dry-run   # report only
    python scripts/backfill_app_owner_grants.py             # apply

Staging → prod procedure
------------------------
1. On **staging / main**, run ``--dry-run`` and confirm the listed Apps are
   the ones you expect (ownerless grants, optional ``--normalize-owner-ids``).
2. Apply without ``--dry-run`` on staging; smoke: open each repaired App as
   the resolved owner and create a Track / edit collaborators.
3. On **prod**, repeat dry-run → apply with the prod DSN. Prefer a one-shot
   exec into the API container (same env as the service) over a laptop
   pointing at prod.
4. Unresolved Apps (no resolvable owner) exit non-zero — decide manually
   before re-running; do not force a grant to a guessed principal.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from typing import List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("backfill_app_owner_grants")


async def _owns_holder_count(app) -> int:
    """How many User→App ``OWNS`` edges target this App.

    Queries edges by ``target`` (same pattern as
    ``sharing._direct_collaborators``) — O(edges-to-app), not O(users).
    """
    from app.models.edges import OWNS

    ctx = await app.get_context()
    rows = await ctx.database.find("edge", {"target": app.id, "entity": OWNS.__name__})
    return sum(1 for row in rows if row.get("source"))


async def _pick_owner(app) -> Tuple[Optional[str], str]:
    """Resolve who should own this App. Returns (graph_user_id, why)."""
    from app.services.permissions import get_user_node
    from app.services.workspace_permissions import get_workspace_owner_user_id

    recorded = str(getattr(app, "owner_user_id", "") or "").strip()
    if recorded:
        user = await get_user_node(recorded)
        if user is not None:
            return user.id, f"recorded owner_user_id {recorded}"

    ws_id = str(getattr(app, "workspace_id", "") or "").strip()
    if ws_id:
        ws_owner = await get_workspace_owner_user_id(ws_id)
        if ws_owner:
            user = await get_user_node(ws_owner)
            if user is not None:
                return user.id, f"workspace owner of {ws_id}"

    return None, "no resolvable owner_user_id and no workspace owner"


async def run(dry_run: bool, normalize: bool) -> int:
    from app.models.edges import OWNS
    from app.models.nodes import App

    repaired = 0
    unresolved: List[str] = []

    for app in await App.find({}):
        state = str(getattr(app, "lifecycle_state", "active") or "active")
        if state != "active":
            continue

        name = getattr(app, "name", None) or getattr(app, "title", None)
        holders = await _owns_holder_count(app)
        recorded = str(getattr(app, "owner_user_id", "") or "").strip()
        needs_edge = holders == 0
        needs_normalize = (
            normalize and bool(recorded) and not recorded.startswith("n.User.")
        )

        if not needs_edge and not needs_normalize:
            continue

        owner_id, why = await _pick_owner(app)
        if owner_id is None:
            unresolved.append(f"{name!r} ({app.id}): {why}")
            logger.warning("UNRESOLVED %r (%s) — %s", name, app.id, why)
            continue

        actions = []
        if needs_edge:
            actions.append(f"grant OWNS to {owner_id} ({why})")
        if needs_normalize:
            actions.append(f"normalize owner_user_id {recorded} -> {owner_id}")
        prefix = "[dry-run] " if dry_run else ""
        logger.info("%s%r (%s): %s", prefix, name, app.id, "; ".join(actions))

        if dry_run:
            repaired += 1
            continue

        from app.services.permissions import get_user_node

        user = await get_user_node(owner_id)
        if user is None:  # pragma: no cover — _pick_owner already resolved it
            unresolved.append(f"{name!r} ({app.id}): owner vanished mid-run")
            continue

        now = datetime.now(timezone.utc).isoformat()
        if needs_edge:
            await user.connect(app, edge=OWNS, role="owner", granted_at=now)
        if needs_edge or needs_normalize:
            app.owner_user_id = user.id
            app.updated_at = now
            await app.save()
        repaired += 1

    verb = "would repair" if dry_run else "repaired"
    logger.info("\n%s %d App(s); %d unresolved", verb, repaired, len(unresolved))
    for line in unresolved:
        logger.info("  unresolved: %s", line)
    # Unresolved Apps need a human decision (no owner exists to grant to), so
    # surface them as a non-zero exit rather than reporting a clean run.
    return 1 if unresolved else 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing.",
    )
    parser.add_argument(
        "--normalize-owner-ids",
        action="store_true",
        help=(
            "Also rewrite owner_user_id values holding a non-graph principal "
            "id. Cosmetic — every reader resolves either form."
        ),
    )
    args = parser.parse_args()

    # Same bootstrap as other CLI backfills — without this, App.find() has no
    # default GraphContext and --dry-run fails before reporting anything.
    from app.services.db_init import init_prime_db

    init_prime_db()
    sys.exit(asyncio.run(run(args.dry_run, args.normalize_owner_ids)))


if __name__ == "__main__":
    main()
