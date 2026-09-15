"""Phase 10.5 Plan 10.5-02 — backfill HAS_SHARE_LINK edges.

Idempotent. For every persisted ShareLink that lacks a
``<resource> -HAS_SHARE_LINK-> ShareLink`` edge, resolve the resource
via the scalar (``resource_type`` + ``resource_id``) and wire it.

Usage::

    # Dry-run — print planned wires without writing.
    python -m scripts.backfill_share_link_edges --dry

    # Actual backfill.
    python -m scripts.backfill_share_link_edges

Also invoked automatically on server startup when
``INTEGRAL_BACKFILL_GRAPH_EDGES=1`` is set (a single-pass sweep that
runs once per process boot — see ``app/main.py``).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime
from typing import Optional

from app.models.edges import HAS_SHARE_LINK
from app.models.nodes import App, Entry, ShareLink, Track

logger = logging.getLogger(__name__)


async def _resolve_resource(link: ShareLink):
    cls_map = {"app": App, "track": Track, "entry": Entry}
    cls = cls_map.get(link.resource_type)
    if cls is None:
        return None
    return await cls.get(link.resource_id)


async def backfill_share_link_edges(*, dry_run: bool = False) -> dict:
    """Backfill HAS_SHARE_LINK edges. Returns counters."""
    stats = {"total": 0, "already_wired": 0, "wired": 0, "skipped": 0}
    links = await ShareLink.find()
    stats["total"] = len(links)
    now_iso = datetime.now().isoformat()
    for link in links:
        resource = await _resolve_resource(link)
        if resource is None:
            logger.warning(
                "backfill_share_link_edges: resource missing for link %s (%s:%s)",
                link.id,
                link.resource_type,
                link.resource_id,
            )
            stats["skipped"] += 1
            continue
        ctx = await resource.get_context()
        existing = await ctx.find_edges_between(
            resource.id, link.id, edge_class=HAS_SHARE_LINK
        )
        if existing:
            stats["already_wired"] += 1
            continue
        if dry_run:
            logger.info(
                "[dry] would wire %s:%s -HAS_SHARE_LINK-> %s",
                link.resource_type,
                link.resource_id,
                link.id,
            )
        else:
            await resource.connect(
                link, edge=HAS_SHARE_LINK, attached_at=link.created_at or now_iso
            )
        stats["wired"] += 1
    return stats


async def _main(dry_run: bool) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from app.services.db_init import init_prime_db

    init_prime_db()
    stats = await backfill_share_link_edges(dry_run=dry_run)
    print(f"backfill_share_link_edges {('[dry-run] ' if dry_run else '')}=> {stats}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true", help="dry-run only (no writes)")
    args = parser.parse_args()
    asyncio.run(_main(dry_run=args.dry))
