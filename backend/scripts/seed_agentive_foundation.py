"""Post-reseed agentive + jvagent graph bootstrap (no HTTP server).

Runs the same startup slice ``app.main`` invokes: ``ensure_integral_app_graph`` +
jvagent ``embed.bootstrap``. Use after ``seed_data.py`` if you want agent/action
nodes without waiting for a full backend restart.

The backend lifespan hook runs this automatically on every boot, so this
script is optional — it exists mainly for post-reseed automation.

Usage::

    cd backend
    python -m scripts.seed_agentive_foundation
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Optional

import app.config  # noqa: F401 — load .env before DB / retrieval init

logger = logging.getLogger("seed_agentive_foundation")


def _jvagent_update_mode() -> Optional[str]:
    raw = os.getenv("JVAGENT_UPDATE_MODE", "source").strip().lower()
    if raw not in ("run", "merge", "source"):
        logger.warning("Invalid JVAGENT_UPDATE_MODE %r; falling back to 'source'", raw)
        raw = "source"
    return None if raw == "run" else raw


async def seed_agentive_foundation(*, dry_run: bool = False) -> None:
    from app.services.db_init import init_prime_db

    init_prime_db()

    from app.services.app_graph import ensure_integral_app_graph

    if dry_run:
        logger.info("[dry] would run ensure_integral_app_graph + jvagent bootstrap")
        return

    await ensure_integral_app_graph()

    from jvagent import embed as jvagent_embed

    # Reuse the orphan purge from main.py (imported lazily to avoid Server() init).
    from app.main import _purge_dead_resident_action_orphans

    await _purge_dead_resident_action_orphans()

    backend_dir = Path(__file__).resolve().parents[1]
    agent_root = backend_dir.parent / "agent"
    if not (agent_root / "app.yaml").exists():
        raise FileNotFoundError(f"agent app root not found: {agent_root / 'app.yaml'}")

    update_mode = _jvagent_update_mode()
    await jvagent_embed.bootstrap(
        app_root=agent_root,
        update_mode=update_mode,
        ensure_admin=False,
    )
    logger.info(
        "jvagent embed bootstrap completed (app_root=%s, update_mode=%s)",
        agent_root,
        update_mode or "run",
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log planned steps without writing.",
    )
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
