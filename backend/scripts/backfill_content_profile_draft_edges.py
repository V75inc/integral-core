"""Phase 10.5 Plan 10.5-10 — backfill HAS_DRAFT_PROFILE edges.

Idempotent. For every persisted ContentProfile with ``draft_of_id``
populated (i.e. it was forked from a published parent), wires
``<published> -HAS_DRAFT_PROFILE-> <self>`` if the edge is missing.
Covers both ``status="draft"`` (still in-flight) and
``status="published"`` rows that were promoted via ``publish_draft``
(which preserves the draft node as audit provenance — the edge
remains as the permanent provenance pointer).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime

from app.models.edges import HAS_DRAFT_PROFILE
from app.models.nodes import ContentProfile

logger = logging.getLogger(__name__)


async def backfill_content_profile_draft_edges(*, dry_run: bool = False) -> dict:
    stats = {"total": 0, "already_wired": 0, "wired": 0, "skipped": 0}
    cps = await ContentProfile.find()
    stats["total"] = len(cps)
    now_iso = datetime.now().isoformat()
    for cp in cps:
        parent_id = getattr(cp, "draft_of_id", "") or ""
        if not parent_id:
            continue
        parent = await ContentProfile.get(parent_id)
        if parent is None:
            stats["skipped"] += 1
            continue
        ctx = await parent.get_context()
        existing = await ctx.find_edges_between(
            parent.id, cp.id, edge_class=HAS_DRAFT_PROFILE
        )
        if existing:
            stats["already_wired"] += 1
            continue
        if dry_run:
            logger.info(
                "[dry] would wire published=%s -HAS_DRAFT_PROFILE-> draft=%s",
                parent.id,
                cp.id,
            )
        else:
            await parent.connect(
                cp,
                edge=HAS_DRAFT_PROFILE,
                forked_at=cp.created_at or now_iso,
            )
        stats["wired"] += 1
    return stats


async def _main(dry_run: bool) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from app.services.db_init import init_prime_db

    init_prime_db()
    stats = await backfill_content_profile_draft_edges(dry_run=dry_run)
    print(
        "backfill_content_profile_draft_edges "
        f"{('[dry-run] ' if dry_run else '')}=> {stats}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true")
    args = parser.parse_args()
    asyncio.run(_main(dry_run=args.dry))
