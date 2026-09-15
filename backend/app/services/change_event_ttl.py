"""TTL reclaim for ChangeEvent snapshots (D-04 + D-15) — Plan 02-04 Task 2.

Storage shape: ChangeEvents live as ``DBLog`` rows with
``log_level="CHANGE_EVENT"`` in the logging database (see
``app/services/change_event_logger.py``). Reclaim operates on those rows:

- At ``CHANGE_EVENT_SNAPSHOT_TTL_DAYS`` days post-creation, ``log_data.before``
  and ``log_data.after`` are nulled out.
- The DBLog row itself NEVER deletes — ``log_data.snapshot_reclaimed_at``
  marks the reclaim pass and gates idempotency.
- Metadata fields (``logged_at``, ``event_code``, ``log_data.actor_*``,
  ``log_data.resource_*``, ``log_data.scope``) persist forever as the audit
  trail.

Per CONTEXT D-15 option (a) — FastAPI startup spawns
``asyncio.create_task(ttl_reclaim_loop())`` which wakes every
``CHANGE_EVENT_RECLAIM_INTERVAL_HOURS`` hours. The loop is skipped under
TESTING / PYTEST_CURRENT_TEST (verified at the spawn site in app/main.py).

Each wake sweeps in bounded pages of ``CHANGE_EVENT_RECLAIM_BATCH_SIZE`` rows
up to ``CHANGE_EVENT_RECLAIM_MAX_PER_CYCLE`` rows total, so a first pass over
a large backlog (TTL newly crossed / lowered, or an outage longer than the TTL
window) cannot hydrate the whole matching set at once.

Module-level grep invariant: this module MUST NOT contain ``DBLog.destroy`` or
``DBLog.delete`` — the test ``test_reclaim_does_not_delete_rows_grep_invariant``
enforces this.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

from app.services.change_event_logger import (
    get_change_event_logger,
    is_change_event_enabled,
)
from app.utils.time import utc_now, utc_now_iso

logger = logging.getLogger(__name__)


def _ttl_cutoff() -> datetime:
    """Return the cutoff datetime — anything strictly older has its snapshot nulled."""
    try:
        days = int(os.getenv("CHANGE_EVENT_SNAPSHOT_TTL_DAYS", "90"))
    except ValueError:
        days = 90
    return utc_now() - timedelta(days=days)


def _interval_seconds() -> float:
    """Wake interval for the reclaim loop in seconds (default 6h)."""
    try:
        hours = float(os.getenv("CHANGE_EVENT_RECLAIM_INTERVAL_HOURS", "6"))
    except ValueError:
        hours = 6.0
    return hours * 3600.0


def _int_knob(name: str, fallback: int) -> int:
    """Read a positive int knob from the env, falling back to ``settings``.

    Env is read live (not frozen at import) so the sweep can be re-tuned
    without a restart and so tests can ``monkeypatch.setenv`` it — matching
    how ``CHANGE_EVENT_SNAPSHOT_TTL_DAYS`` behaves above.
    """
    from app.config import settings

    default = int(getattr(settings, name, fallback) or fallback)
    raw = os.getenv(name)
    if raw is None:
        return max(1, default)
    try:
        return max(1, int(raw))
    except ValueError:
        return max(1, default)


def _batch_size() -> int:
    """Rows hydrated + saved per reclaim query (default 500)."""
    return _int_knob("CHANGE_EVENT_RECLAIM_BATCH_SIZE", 500)


def _max_per_cycle() -> int:
    """Hard cap on rows one wake may reclaim (default 10000)."""
    return _int_knob("CHANGE_EVENT_RECLAIM_MAX_PER_CYCLE", 10000)


async def reclaim_old_snapshots() -> int:
    """Null ``log_data.before/after`` on CHANGE_EVENT rows older than the TTL.

    Returns the number of rows reclaimed in this pass. Idempotent — rows
    already reclaimed (``log_data.snapshot_reclaimed_at`` set) are skipped.

    NEVER deletes rows — only mutates the snapshot + ``snapshot_reclaimed_at``
    fields inside ``log_data``. D-04 invariant: append-only metadata,
    snapshot-only reclaim.
    """
    if not is_change_event_enabled():
        return 0

    ce_logger = get_change_event_logger()
    cutoff = _ttl_cutoff()
    batch_size = _batch_size()
    max_per_cycle = _max_per_cycle()
    reclaimed = 0
    now_iso = utc_now_iso()

    # Paged sweep. The query predicate bounds the STEADY state, but the first
    # pass after the TTL is crossed / lowered, or after an outage longer than
    # the TTL window, can match an arbitrarily large backlog — hydrating that
    # whole set into one list (and then saving it row-by-row with no yield)
    # is what this loop exists to prevent. Reclaimed rows fall out of the
    # predicate, so successive pages make progress without a cursor; a page
    # that reclaims nothing terminates the cycle so a non-dropping row can
    # never spin the loop.
    while reclaimed < max_per_cycle:
        page_limit = min(batch_size, max_per_cycle - reclaimed)
        # Filtered at the query — only rows past the cutoff with no
        # ``snapshot_reclaimed_at`` are hydrated. The per-row checks below
        # stay as the authority (the DB-side timestamp compare is
        # string-based).
        rows = await ce_logger.find_reclaimable(cutoff=cutoff, limit=page_limit)
        if not rows:
            break

        page_reclaimed = 0
        for row in rows:
            row_ts = row.logged_at
            if row_ts is None:
                continue
            if isinstance(row_ts, str):
                try:
                    row_ts = datetime.fromisoformat(row_ts)
                except ValueError:
                    logger.warning(
                        "change_event_ttl: unparseable logged_at on row %s — skipping",
                        row.id,
                    )
                    continue
            if row_ts.tzinfo is None:
                row_ts = row_ts.replace(tzinfo=timezone.utc)
            if row_ts > cutoff:
                continue  # too recent — skip
            data = dict(row.log_data or {})
            if data.get("snapshot_reclaimed_at"):
                continue  # already reclaimed — idempotent skip

            data["before"] = None
            data["after"] = None
            data["snapshot_reclaimed_at"] = now_iso
            row.log_data = data
            await row.save()
            page_reclaimed += 1

        reclaimed += page_reclaimed
        if page_reclaimed == 0:
            # Nothing in this page was reclaimable (all skipped, or the
            # predicate is not dropping already-reclaimed rows) — stop rather
            # than re-fetching the same page forever.
            break
        if len(rows) < page_limit:
            break  # short page — backlog drained
        # Yield to the event loop between pages so a large backlog sweep
        # never monopolises the loop.
        await asyncio.sleep(0)

    if reclaimed >= max_per_cycle:
        logger.warning(
            "change_event_ttl: hit per-cycle cap of %d rows — remaining backlog "
            "will be reclaimed on the next pass",
            max_per_cycle,
        )

    if reclaimed:
        logger.info(
            "change_event_ttl: reclaimed %d snapshots older than %s",
            reclaimed,
            cutoff.isoformat(),
        )
    return reclaimed


async def ttl_reclaim_loop() -> None:
    """Background loop — wakes every interval, calls reclaim_old_snapshots."""
    interval = _interval_seconds()
    logger.info("change_event_ttl: reclaim loop started (interval=%.1fs)", interval)
    while True:
        try:
            await reclaim_old_snapshots()
        except Exception as e:
            logger.warning("change_event_ttl: reclaim cycle failed: %s", e)
        await asyncio.sleep(interval)
