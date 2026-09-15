"""Phase 10.5 Plan 10.5-08 — backfill HAS_CHANNEL_IDENTITY edges.

Idempotent. For every persisted ChannelIdentity missing the
``User -HAS_CHANNEL_IDENTITY-> ChannelIdentity`` edge, resolves the
User via the ``user_id`` scalar and wires it. The agentive-layer
edge (``app.agentive.edges.HasChannelIdentity``) is the canonical
class used at the service-layer create sites; the gap was the
channels.py REST endpoint plus any pre-Plan-10.5-08 rows.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime

from app.agentive.edges import HAS_CHANNEL_IDENTITY
from app.agentive.nodes import ChannelIdentity
from app.services.permissions import get_user_node

logger = logging.getLogger(__name__)


async def backfill_channel_identity_edges(*, dry_run: bool = False) -> dict:
    stats = {"total": 0, "already_wired": 0, "wired": 0, "skipped": 0}
    cis = await ChannelIdentity.find()
    stats["total"] = len(cis)
    now_iso = datetime.now().isoformat()
    for ci in cis:
        user = await get_user_node(ci.user_id) if ci.user_id else None
        if user is None:
            stats["skipped"] += 1
            continue
        ctx = await user.get_context()
        existing = await ctx.find_edges_between(
            user.id, ci.id, edge_class=HAS_CHANNEL_IDENTITY
        )
        if existing:
            stats["already_wired"] += 1
            continue
        if dry_run:
            logger.info(
                "[dry] would wire user=%s -HAS_CHANNEL_IDENTITY-> %s", user.id, ci.id
            )
        else:
            await user.connect(
                ci,
                edge=HAS_CHANNEL_IDENTITY,
                is_primary=False,
                created_at=ci.created_at or now_iso,
            )
        stats["wired"] += 1
    return stats


async def _main(dry_run: bool) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from app.services.db_init import init_prime_db

    init_prime_db()
    stats = await backfill_channel_identity_edges(dry_run=dry_run)
    print(
        f"backfill_channel_identity_edges {('[dry-run] ' if dry_run else '')}=> {stats}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true")
    args = parser.parse_args()
    asyncio.run(_main(dry_run=args.dry))
