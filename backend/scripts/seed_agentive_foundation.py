"""Post-reseed Core graph and native skill validation (no HTTP server)."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Optional

import app.config  # noqa: F401 — load configuration before DB init

logger = logging.getLogger("seed_agentive_foundation")


async def seed_agentive_foundation(*, dry_run: bool = False) -> None:
    from app.services.app_graph import ensure_integral_app_graph
    from app.services.db_init import init_prime_db
    from app.services.skill_compliance import list_core_skills

    init_prime_db()
    if not list_core_skills():
        raise RuntimeError("Native Integral skills are missing")
    if dry_run:
        logger.info("[dry] would ensure the rooted Integral graph")
        return
    await ensure_integral_app_graph()
    logger.info("Integral graph and native skills ready")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        asyncio.run(seed_agentive_foundation(dry_run=args.dry_run))
    except Exception as exc:
        logger.error("seed_agentive_foundation failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
