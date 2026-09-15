"""Phase 10.5 Plan 10.5-04 — backfill HAS_CONFLICT edges.

Idempotent. For every persisted Conflict that lacks an
``Entry -HAS_CONFLICT-> Conflict`` edge, resolves the Entry via
``entry_id`` and wires it.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime

from app.models.edges import HAS_CONFLICT
from app.models.nodes import Conflict, Entry

logger = logging.getLogger(__name__)


async def backfill_conflict_edges(*, dry_run: bool = False) -> dict:
    stats = {"total": 0, "already_wired": 0, "wired": 0, "skipped": 0}
    conflicts = await Conflict.find()
    stats["total"] = len(conflicts)
    now_iso = datetime.now().isoformat()
    for c in conflicts:
        entry = await Entry.get(c.entry_id) if c.entry_id else None
        if entry is None:
            stats["skipped"] += 1
            continue
        ctx = await entry.get_context()
        existing = await ctx.find_edges_between(entry.id, c.id, edge_class=HAS_CONFLICT)
        if existing:
            stats["already_wired"] += 1
            continue
        if dry_run:
            logger.info("[dry] would wire entry=%s -HAS_CONFLICT-> %s", entry.id, c.id)
        else:
            await entry.connect(
                c, edge=HAS_CONFLICT, detected_at=c.detected_at or now_iso
            )
        stats["wired"] += 1
    return stats


async def _main(dry_run: bool) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from app.services.db_init import init_prime_db

    init_prime_db()
    stats = await backfill_conflict_edges(dry_run=dry_run)
    print(f"backfill_conflict_edges {('[dry-run] ' if dry_run else '')}=> {stats}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true")
    args = parser.parse_args()
    asyncio.run(_main(dry_run=args.dry))
