"""Phase 10.5 Plan 10.5-07 — backfill ConversationContext CONTAINS edges.

Idempotent. For every persisted ConversationContext missing a
``<parent> -CONTAINS-> ConversationContext`` edge:

  * If ``agent_config_id`` resolves → AgentConfig -CONTAINS-> ctx.
  * Else if ``user_id`` resolves    → User -CONTAINS-> ctx.
  * Else                             → skipped (audit walker logs).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime

from app.agentive.nodes import AgentConfig, ConversationContext
from app.models.edges import CONTAINS
from app.services.permissions import get_user_node

logger = logging.getLogger(__name__)


async def backfill_conversation_context_edges(*, dry_run: bool = False) -> dict:
    stats = {"total": 0, "already_wired": 0, "wired": 0, "skipped": 0}
    ctxs = await ConversationContext.find()
    stats["total"] = len(ctxs)
    now_iso = datetime.now().isoformat()
    for ctx_node in ctxs:
        parent = None
        if getattr(ctx_node, "agent_config_id", None):
            parent = await AgentConfig.get(ctx_node.agent_config_id)
        if parent is None and ctx_node.user_id:
            parent = await get_user_node(ctx_node.user_id)
        if parent is None:
            stats["skipped"] += 1
            continue
        graph_ctx = await parent.get_context()
        existing = await graph_ctx.find_edges_between(
            parent.id, ctx_node.id, edge_class=CONTAINS
        )
        if existing:
            stats["already_wired"] += 1
            continue
        if dry_run:
            logger.info("[dry] would wire %s -CONTAINS-> %s", parent.id, ctx_node.id)
        else:
            await parent.connect(
                ctx_node,
                edge=CONTAINS,
                added_at=ctx_node.created_at or now_iso,
            )
        stats["wired"] += 1
    return stats


async def _main(dry_run: bool) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from app.services.db_init import init_prime_db

    init_prime_db()
    stats = await backfill_conversation_context_edges(dry_run=dry_run)
    print(
        "backfill_conversation_context_edges "
        f"{('[dry-run] ' if dry_run else '')}=> {stats}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true")
    args = parser.parse_args()
    asyncio.run(_main(dry_run=args.dry))
