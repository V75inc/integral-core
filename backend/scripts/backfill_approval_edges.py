"""Phase 10.5 Plan 10.5-05 — backfill HAS_APPROVAL + HAS_APPROVAL_DECISION edges.

Idempotent. For every persisted Approval:
  - Wires Policy -HAS_APPROVAL-> Approval via the policy_id scalar.
  - When status in {approved, rejected} and decider_id is set, also
    wires User -HAS_APPROVAL_DECISION-> Approval.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime

from app.models.edges import HAS_APPROVAL, HAS_APPROVAL_DECISION
from app.models.nodes import Approval, Policy
from app.services.permissions import get_user_node

logger = logging.getLogger(__name__)


async def backfill_approval_edges(*, dry_run: bool = False) -> dict:
    stats = {
        "total": 0,
        "approval_wired": 0,
        "approval_already": 0,
        "decision_wired": 0,
        "decision_already": 0,
        "skipped": 0,
    }
    approvals = await Approval.find()
    stats["total"] = len(approvals)
    now_iso = datetime.now().isoformat()
    for ap in approvals:
        # HAS_APPROVAL
        if ap.policy_id:
            policy = await Policy.get(ap.policy_id)
            if policy is not None:
                ctx = await policy.get_context()
                existing = await ctx.find_edges_between(
                    policy.id, ap.id, edge_class=HAS_APPROVAL
                )
                if existing:
                    stats["approval_already"] += 1
                elif dry_run:
                    logger.info(
                        "[dry] would wire policy=%s -HAS_APPROVAL-> %s",
                        policy.id,
                        ap.id,
                    )
                    stats["approval_wired"] += 1
                else:
                    await policy.connect(
                        ap, edge=HAS_APPROVAL, created_at=ap.created_at or now_iso
                    )
                    stats["approval_wired"] += 1
            else:
                stats["skipped"] += 1

        # HAS_APPROVAL_DECISION (only on decided rows with a decider)
        if ap.status in ("approved", "rejected") and ap.decider_id:
            decider = await get_user_node(ap.decider_id)
            if decider is not None:
                ctx = await decider.get_context()
                existing = await ctx.find_edges_between(
                    decider.id, ap.id, edge_class=HAS_APPROVAL_DECISION
                )
                if existing:
                    stats["decision_already"] += 1
                elif dry_run:
                    logger.info(
                        "[dry] would wire user=%s -HAS_APPROVAL_DECISION-> %s",
                        decider.id,
                        ap.id,
                    )
                    stats["decision_wired"] += 1
                else:
                    await decider.connect(
                        ap,
                        edge=HAS_APPROVAL_DECISION,
                        decided_at=ap.decided_at or now_iso,
                        decision=ap.status,
                    )
                    stats["decision_wired"] += 1
    return stats


async def _main(dry_run: bool) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from app.services.db_init import init_prime_db

    init_prime_db()
    stats = await backfill_approval_edges(dry_run=dry_run)
    print(f"backfill_approval_edges {('[dry-run] ' if dry_run else '')}=> {stats}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true")
    args = parser.parse_args()
    asyncio.run(_main(dry_run=args.dry))
