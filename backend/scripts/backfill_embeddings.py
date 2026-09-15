"""Re-embed every Entry into the active embedding store.

Use cases:

1. **Cold seed / store cutover.** A fresh deployment (or a deployment
   switching its embedding store — e.g. the Postgres + pgvector driver
   landing on an existing graph) needs to hydrate the vector index before
   semantic / hybrid retrieval returns useful results. The write hook
   (``app/api/entries.py:_reembed_entry``) embeds NEW / updated entries
   automatically, but pre-existing entries were never embedded — this
   script backfills them.
2. **Backend swap.** The runtime moved from sentence-transformers/torch
   to fastembed/ONNX. Vectors are dimension-compatible (384) but not
   bitwise identical across backends; a full re-embed is the correct
   cutover regardless of prior store contents.
3. **Recovery.** If the embedding store loses rows (table / collection
   drop, migration), this script restores them from canonical Entry data.

The script is **idempotent** — every Entry is upserted by id (the
pgvector / atlas drivers ``ON CONFLICT`` / ``upsert`` on the entry id),
so re-runs harmlessly overwrite. Failures are logged-and-skipped (mirrors
the api/entries.py ``_reembed_entry`` discipline); the script does NOT
roll the run back on a single failure. Final counters land in stdout.

The resolved store MUST be a real vector store (not ``null``) — the
script asserts ``semantic_retrieval_available()`` and exits non-zero with
a clear message otherwise (running against ``null`` would silently drop
every upsert). On Postgres set ``JVSPATIAL_DB_TYPE=postgres`` +
``JVSPATIAL_POSTGRES_DSN`` (the ``pgvector`` driver then auto-registers
and ``EMBEDDING_STORE_DRIVER=auto`` resolves to it).

Usage::

    # Dry-run — composes embeddings + sanity-checks the model, no writes.
    python -m scripts.backfill_embeddings --dry

    # Full backfill against the active store.
    python -m scripts.backfill_embeddings

    # Scope to one workspace / one track (mutually narrowing).
    python -m scripts.backfill_embeddings --workspace ws_123
    python -m scripts.backfill_embeddings --track t_456

    # Smoke-test a small slice before the full run.
    python -m scripts.backfill_embeddings --limit 5

    # Tunable progress-logging cadence.
    python -m scripts.backfill_embeddings --log-every 100
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Any, Dict

# Load backend/.env + repo-root .env before ``app.services.retrieval`` registers
# drivers at import time (pgvector only registers when JVSPATIAL_* are set).
import app.config  # noqa: F401

logger = logging.getLogger("backfill_embeddings")


class _StoreUnavailableError(RuntimeError):
    """Raised when the resolved store is ``null`` (semantic unavailable)."""


async def _load_entries(
    *,
    workspace_id: str = "",
    track_id: str = "",
):
    """Return the Entry list to backfill, narrowed by workspace / track.

    ``track_id`` narrows directly via the denormalized ``context.track_id``
    column. ``workspace_id`` resolves each candidate entry's parent track
    to its effective workspace (``Track.workspace_id`` or the parent App's)
    and keeps only matches — mirrors the read-path scoping helper in
    ``services/request_scope``.
    """
    from app.models.nodes import Entry, Track
    from app.services.request_scope import _effective_workspace_id_of

    if track_id:
        return await Entry.find({"context.track_id": track_id})

    entries = await Entry.find()
    if not workspace_id:
        return entries

    # Resolve each entry's parent track once (cache by track_id) and keep
    # only entries whose track resolves to the target workspace.
    track_ws_cache: Dict[str, str] = {}
    kept = []
    for entry in entries:
        tid = getattr(entry, "track_id", "") or ""
        if not tid:
            continue
        if tid not in track_ws_cache:
            track = await Track.get(tid)
            track_ws_cache[tid] = (
                _effective_workspace_id_of(track) if track is not None else ""
            ) or ""
        if track_ws_cache[tid] == workspace_id:
            kept.append(entry)
    return kept


async def backfill_embeddings(
    *,
    dry_run: bool = False,
    log_every: int = 50,
    workspace_id: str = "",
    track_id: str = "",
    limit: int = 0,
) -> Dict[str, int]:
    """Re-embed Entries into the active embedding store.

    Returns counters: ``total``, ``embedded``, ``skipped``, ``failed``.
    Safe to call repeatedly (upsert is idempotent on the entry id).

    Raises ``_StoreUnavailableError`` when the resolved driver is ``null``
    — running against it would silently drop every upsert, so the caller
    must wire a real vector store (pgvector / atlas) first.
    """
    from app.services.retrieval import (
        get_embedding_store,
        semantic_retrieval_available,
    )
    from app.services.retrieval.embedding_model import embed_entry_text

    stats = {"total": 0, "embedded": 0, "skipped": 0, "failed": 0}

    if not semantic_retrieval_available():
        raise _StoreUnavailableError(
            "the resolved embedding store is 'null' (semantic retrieval "
            "unavailable) — every upsert would be a no-op. On Postgres set "
            "JVSPATIAL_DB_TYPE=postgres + JVSPATIAL_POSTGRES_DSN so the "
            "'pgvector' driver registers and EMBEDDING_STORE_DRIVER=auto "
            "resolves to it; on Mongo use the 'atlas' driver. Aborting."
        )

    store = get_embedding_store()
    entries = await _load_entries(workspace_id=workspace_id, track_id=track_id)
    if limit and limit > 0:
        entries = entries[:limit]
    stats["total"] = len(entries)
    logger.info(
        "backfill_embeddings: starting (dry_run=%s, workspace=%s, track=%s, "
        "limit=%s, entries=%d)",
        dry_run,
        workspace_id or "-",
        track_id or "-",
        limit or "-",
        stats["total"],
    )

    for idx, entry in enumerate(entries, start=1):
        track = getattr(entry, "track_id", "") or ""
        if not track:
            # No track_id → cannot satisfy the I-RET-04 pre-filter
            # contract. Skip rather than upsert an unindexed row.
            logger.warning(
                "backfill_embeddings: entry %s has empty track_id; skipping",
                entry.id,
            )
            stats["skipped"] += 1
            continue
        try:
            # Skip entries with no embeddable text (empty title/body/fields)
            # — embedding a blank string produces noise, not signal.
            vector = await embed_entry_text(entry)
            if not vector or not any(vector):
                logger.info(
                    "backfill_embeddings: entry %s has empty embed text; skipping",
                    entry.id,
                )
                stats["skipped"] += 1
                continue
            if not dry_run:
                await store.upsert(
                    entry_id=entry.id,
                    vector=vector,
                    metadata={
                        "track_id": track,
                        "type_id": getattr(entry, "type_id", "") or "",
                    },
                )
            stats["embedded"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "backfill_embeddings: entry %s failed: %s",
                entry.id,
                exc,
            )
            stats["failed"] += 1

        if idx % log_every == 0:
            logger.info(
                "backfill_embeddings: progress %d/%d (embedded=%d skipped=%d failed=%d)",
                idx,
                stats["total"],
                stats["embedded"],
                stats["skipped"],
                stats["failed"],
            )

    logger.info(
        "backfill_embeddings: complete %s",
        stats,
    )
    return stats


def _parse_args(argv: Any = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="backfill_embeddings",
        description="Re-embed every Entry into the active embedding store.",
    )
    parser.add_argument(
        "--dry",
        action="store_true",
        dest="dry_run",
        help="Compute embeddings but do NOT write to the store.",
    )
    parser.add_argument(
        "--workspace",
        default="",
        help="Only backfill entries whose parent track is in this workspace id.",
    )
    parser.add_argument(
        "--track",
        default="",
        help="Only backfill entries in this track id.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Cap the number of entries processed (0 = no cap; for smoke tests).",
    )
    parser.add_argument(
        "--log-every",
        type=int,
        default=50,
        help="Emit a progress line every N entries (default 50).",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="DEBUG-level logging.",
    )
    return parser.parse_args(argv)


def main(argv: Any = None) -> int:
    """CLI entrypoint — parse args, run the backfill, map result to exit code."""
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    from app.services.db_init import init_prime_db

    init_prime_db()
    try:
        stats = asyncio.run(
            backfill_embeddings(
                dry_run=args.dry_run,
                log_every=args.log_every,
                workspace_id=args.workspace,
                track_id=args.track,
                limit=args.limit,
            )
        )
    except _StoreUnavailableError as exc:
        logger.error("backfill_embeddings: %s", exc)
        return 2
    # Non-zero exit when every Entry failed — surfaces a broken model /
    # store config to operators / CI cleanly.
    if stats["total"] > 0 and stats["embedded"] == 0 and stats["failed"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
