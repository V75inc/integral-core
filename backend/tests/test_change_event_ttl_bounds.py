"""TTL reclaim must sweep in BOUNDED pages, never one unbounded fetch (#31).

``find_reclaimable``'s predicate bounds the steady state, but the first pass
after the TTL is crossed / lowered, or after an outage longer than the TTL
window, matches an arbitrarily large backlog. Before this fix
``ctx.database.find("object", query)`` carried no ``limit``, ``find_reclaimable``
exposed no cap, and ``reclaim_old_snapshots`` had no page loop — so that first
pass hydrated EVERY matching row into one list and saved them serially.

These tests assert the bound itself: the query carries a limit, the sweep
issues multiple bounded queries for a backlog larger than the batch size, and
the per-cycle cap stops one wake from running unbounded.
"""

from __future__ import annotations

import inspect
from datetime import timedelta
from typing import Any, Dict, List

import pytest

from app.services.change_event_logger import (
    CHANGE_EVENT_LOG_LEVEL,
    get_change_event_logger,
)
from app.services.change_event_ttl import reclaim_old_snapshots
from app.utils.time import utc_now


async def _seed_stale_rows(count: int, *, prefix: str, days_old: int = 91) -> List[str]:
    """Persist ``count`` backdated CHANGE_EVENT DBLog rows; return their ids."""
    from jvspatial.core.context import GraphContext
    from jvspatial.db import get_database_manager
    from jvspatial.logging.models import DBLog

    log_db = get_database_manager().get_database("logs")
    log_ctx = GraphContext(database=log_db)
    await log_ctx.ensure_indexes(DBLog)

    backdated = utc_now() - timedelta(days=days_old)
    ids: List[str] = []
    for i in range(count):
        entry = DBLog(
            status_code=None,
            event_code="entry.create",
            log_level=CHANGE_EVENT_LOG_LEVEL,
            path=f"user:{prefix}",
            method="",
            logged_at=backdated,
            log_data={
                "message": "entry.create",
                "log_level": CHANGE_EVENT_LOG_LEVEL,
                "ts": backdated.isoformat(),
                "actor_kind": "human",
                "actor_id": prefix,
                "resource_type": "Entry",
                "resource_id": f"{prefix}-{i}",
                "scope": f"user:{prefix}",
                "before": None,
                "after": {"id": f"{prefix}-{i}"},
                "snapshot_reclaimed_at": None,
            },
        )
        await entry.set_context(log_ctx)
        await entry.save()
        ids.append(entry.id)
    return ids


def test_find_reclaimable_requires_a_limit():
    """The signature exposes a mandatory cap — no unbounded call site possible."""
    from app.services.change_event_logger import ChangeEventLogger

    sig = inspect.signature(ChangeEventLogger.find_reclaimable)
    assert "limit" in sig.parameters, "find_reclaimable exposes no limit"
    assert (
        sig.parameters["limit"].default is inspect.Parameter.empty
    ), "limit must be mandatory so no caller can fetch unbounded"


@pytest.mark.asyncio
async def test_find_reclaimable_passes_limit_and_sort_to_the_database(monkeypatch):
    """The DB query carries limit + a deterministic sort, not a bare predicate."""
    ce_logger = get_change_event_logger()
    ctx = ce_logger._get_log_context()
    assert ctx is not None

    captured: Dict[str, Any] = {}
    original_find = ctx.database.find

    async def spy_find(collection, query, **kwargs):
        captured["collection"] = collection
        captured["kwargs"] = kwargs
        return await original_find(collection, query, **kwargs)

    monkeypatch.setattr(ctx.database, "find", spy_find)
    await ce_logger.find_reclaimable(cutoff=utc_now(), limit=7)

    assert captured["kwargs"].get("limit") == 7, captured
    assert captured["kwargs"].get("sort") == [("id", 1)], captured


@pytest.mark.asyncio
async def test_reclaim_pages_instead_of_one_unbounded_fetch(monkeypatch):
    """A backlog larger than the batch size is swept via several bounded queries."""
    monkeypatch.setenv("CHANGE_EVENT_SNAPSHOT_TTL_DAYS", "90")
    monkeypatch.setenv("CHANGE_EVENT_RECLAIM_BATCH_SIZE", "3")
    monkeypatch.setenv("CHANGE_EVENT_RECLAIM_MAX_PER_CYCLE", "10000")

    await _seed_stale_rows(7, prefix="ce-page")

    ce_logger = get_change_event_logger()
    limits: List[Any] = []
    original = type(ce_logger).find_reclaimable

    async def spy(self, *, cutoff, limit):
        limits.append(limit)
        return await original(self, cutoff=cutoff, limit=limit)

    monkeypatch.setattr(type(ce_logger), "find_reclaimable", spy)

    reclaimed = await reclaim_old_snapshots()

    assert reclaimed >= 7, f"expected the backlog swept, got {reclaimed}"
    assert len(limits) >= 3, (
        "reclaim issued a single fetch for a 7-row backlog with batch size 3 — "
        f"query limits seen: {limits}"
    )
    assert all(
        lim <= 3 for lim in limits
    ), f"a query exceeded the configured batch size: {limits}"


@pytest.mark.asyncio
async def test_reclaim_respects_per_cycle_cap(monkeypatch):
    """One wake never reclaims more than CHANGE_EVENT_RECLAIM_MAX_PER_CYCLE rows."""
    monkeypatch.setenv("CHANGE_EVENT_SNAPSHOT_TTL_DAYS", "90")
    monkeypatch.setenv("CHANGE_EVENT_RECLAIM_BATCH_SIZE", "2")
    monkeypatch.setenv("CHANGE_EVENT_RECLAIM_MAX_PER_CYCLE", "4")

    await _seed_stale_rows(9, prefix="ce-cap")

    reclaimed = await reclaim_old_snapshots()
    assert reclaimed == 4, f"per-cycle cap not enforced (reclaimed {reclaimed})"

    # The remainder is picked up by the next wake — no work is lost.
    second = await reclaim_old_snapshots()
    assert second > 0, "backlog remainder was not reclaimed on the next pass"


@pytest.mark.asyncio
async def test_reclaim_batch_knobs_default_when_env_is_garbage(monkeypatch):
    """Unparseable knobs fall back to the settings default rather than crashing."""
    from app.config import settings
    from app.services.change_event_ttl import _batch_size, _max_per_cycle

    monkeypatch.setenv("CHANGE_EVENT_RECLAIM_BATCH_SIZE", "not-a-number")
    monkeypatch.setenv("CHANGE_EVENT_RECLAIM_MAX_PER_CYCLE", "")
    assert _batch_size() == settings.CHANGE_EVENT_RECLAIM_BATCH_SIZE
    assert _max_per_cycle() == settings.CHANGE_EVENT_RECLAIM_MAX_PER_CYCLE

    monkeypatch.setenv("CHANGE_EVENT_RECLAIM_BATCH_SIZE", "0")
    assert _batch_size() >= 1, "a zero batch size would spin the sweep forever"
