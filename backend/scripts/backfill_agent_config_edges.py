"""Phase 10.5 Plan 10.5-06 — backfill AgentConfig attachment edges.

Idempotent. For every persisted AgentConfig missing its canonical
structural edge, dispatches via
``wire_agent_config_attachment_edge`` (the same helper used by the
runtime create/update paths). Selection rules:

  * ``app_id`` set         → App —CONTAINS→ AgentConfig
  * ``scope=personal``     → User —HAS_AGENT_CONFIG→ AgentConfig
  * ``scope=org_facing``   → Workspace —HAS_ORG_AGENT→ AgentConfig
  * ``scope=system``       → IntegralApp —HAS_SYSTEM_AGENT→ AgentConfig
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from app.agentive.nodes import AgentConfig
from app.agentive.services.agent_registry_node import (
    wire_agent_config_attachment_edge,
)

logger = logging.getLogger(__name__)


async def backfill_agent_config_edges(*, dry_run: bool = False) -> dict:
    stats = {"total": 0, "wired": 0, "already_or_skipped": 0}
    configs = await AgentConfig.find()
    stats["total"] = len(configs)
    for cfg in configs:
        if dry_run:
            logger.info(
                "[dry] would dispatch attach helper for %s (scope=%s app_id=%s)",
                cfg.id,
                getattr(cfg, "scope", ""),
                getattr(cfg, "app_id", None),
            )
            stats["wired"] += 1
            continue
        wired = await wire_agent_config_attachment_edge(cfg)
        if wired:
            stats["wired"] += 1
        else:
            stats["already_or_skipped"] += 1
    return stats


async def _main(dry_run: bool) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from app.services.db_init import init_prime_db

    init_prime_db()
    stats = await backfill_agent_config_edges(dry_run=dry_run)
    print(f"backfill_agent_config_edges {('[dry-run] ' if dry_run else '')}=> {stats}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true")
    args = parser.parse_args()
    asyncio.run(_main(dry_run=args.dry))
