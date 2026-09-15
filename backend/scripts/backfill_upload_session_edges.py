"""Phase 10.5 Plan 10.5-03 — backfill HAS_UPLOAD_SESSION edges.

Idempotent. For every persisted UploadSession with status in
{pending, active} that lacks an ``Entry -HAS_UPLOAD_SESSION-> UploadSession``
edge, resolves the Entry via ``entry_id`` and wires it. Terminal
sessions (complete / expired / cancelled) are typically deleted shortly
after — the script skips them.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime

from app.models.edges import HAS_UPLOAD_SESSION
from app.models.nodes import Entry, UploadSession

logger = logging.getLogger(__name__)

_TERMINAL = {"complete", "expired", "cancelled"}


async def backfill_upload_session_edges(*, dry_run: bool = False) -> dict:
    stats = {"total": 0, "already_wired": 0, "wired": 0, "skipped": 0}
    sessions = await UploadSession.find()
    stats["total"] = len(sessions)
    now_iso = datetime.now().isoformat()
    for sess in sessions:
        if getattr(sess, "status", "") in _TERMINAL:
            stats["skipped"] += 1
            continue
        entry = await Entry.get(sess.entry_id) if sess.entry_id else None
        if entry is None:
            stats["skipped"] += 1
            continue
        ctx = await entry.get_context()
        existing = await ctx.find_edges_between(
            entry.id, sess.id, edge_class=HAS_UPLOAD_SESSION
        )
        if existing:
            stats["already_wired"] += 1
            continue
        if dry_run:
            logger.info(
                "[dry] would wire entry=%s -HAS_UPLOAD_SESSION-> %s", entry.id, sess.id
            )
        else:
            await entry.connect(
                sess,
                edge=HAS_UPLOAD_SESSION,
                started_at=sess.created_at or now_iso,
            )
        stats["wired"] += 1
    return stats


async def _main(dry_run: bool) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from app.services.db_init import init_prime_db

    init_prime_db()
    stats = await backfill_upload_session_edges(dry_run=dry_run)
    print(
        f"backfill_upload_session_edges {('[dry-run] ' if dry_run else '')}=> {stats}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true", help="dry-run only (no writes)")
    args = parser.parse_args()
    asyncio.run(_main(dry_run=args.dry))
