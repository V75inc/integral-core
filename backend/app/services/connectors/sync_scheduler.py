"""Phase 5 Plan 05-03 — asyncio sync_loop background scheduler.

Mirrors Phase 2's ``ttl_reclaim_loop`` precedent at
``app/services/change_event_ttl.py:115-124``. Spawned at startup from
``app/main.py`` next to the TTL loop, inside the SAME TESTING gate. The
function-scope ``PYTEST_CURRENT_TEST`` / ``TESTING`` guard at the top of
``_startup`` short-circuits BEFORE the spawn site is reached, so test runs
never accidentally hit external APIs (Pitfall 6 — T-05-03-05 mitigation).

Locked decision §Q9 — AGENTIVE_ENABLED independence: the scheduler spawns
unconditionally. Connector subclasses live under ``app/agentive/connectors/``
and only register their slugs when AGENTIVE_ENABLED=1. In a non-agentive
boot the SyncConnector registry is empty AND `Connector.find()` returns
zero rows (the Connector Node is itself AGENTIVE_ENABLED-gated), so each
tick is a cheap no-op.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


def _tick_interval_seconds() -> float:
    """Override via ``CONNECTOR_SYNC_TICK_SECONDS`` env (default 60s).

    The tick interval is the GRANULARITY of the loop — each connector's
    own ``sync_interval_seconds`` decides whether THIS tick dispatches a
    pull for it. So a 60s tick + 300s per-connector interval = each
    connector is checked every 60s but only pulled every 5 minutes.
    """
    try:
        return float(os.environ.get("CONNECTOR_SYNC_TICK_SECONDS", "60"))
    except ValueError:
        return 60.0


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def _maybe_dispatch_one(connector) -> bool:
    """Returns True iff this tick dispatched a sync for ``connector``.

    A connector is eligible when EITHER (a) it has never been synced
    (``last_synced_at is None``) OR (b) the elapsed seconds since the last
    sync >= its configured ``sync_interval_seconds``. Dispatch is
    fire-and-forget — each connector's pull is its own asyncio task so a
    slow external API doesn't block the loop tick.
    """
    last = _parse_iso(getattr(connector, "last_synced_at", None))
    interval = float(getattr(connector, "sync_interval_seconds", 300) or 300)
    now = _utc_now()
    if last is not None and (now - last).total_seconds() < interval:
        return False
    # Lazy import keeps module load cheap — sync_runtime pulls in heavy
    # change-event / provenance machinery that only matters at dispatch time.
    from app.services.connectors.sync_runtime import sync_one_connector

    asyncio.create_task(sync_one_connector(connector))
    return True


async def _tick_once() -> int:
    """Single sync_loop tick body — iterate Connectors, dispatch eligible ones.

    Returns the number of dispatches fired this tick (useful for tests).
    Failures during enumeration are logged + swallowed; the next tick will
    re-attempt. Failures during a per-connector dispatch are swallowed by
    the asyncio task wrapper — each ``sync_one_connector`` call manages its
    own ``connector.sync.failed`` emit.
    """
    dispatched = 0
    try:
        # Lazy import — the Connector Node is AGENTIVE_ENABLED-gated; in
        # a non-agentive boot this import works (the class is always
        # available) but ``Connector.find()`` returns zero (no rows
        # persisted) so the tick is a clean no-op.
        from app.agentive.nodes import Connector

        connectors = await Connector.find()
        for connector in connectors:
            if await _maybe_dispatch_one(connector):
                dispatched += 1
    except Exception as exc:  # noqa: BLE001
        logger.warning("connector sync_loop: tick failed: %s", exc)
    return dispatched


async def sync_loop() -> None:
    """Background coroutine — wakes every tick, dispatches eligible Connectors.

    Mirror of ``change_event_ttl.ttl_reclaim_loop`` shape (Phase 2 02-04).
    The infinite loop never exits — the asyncio task is cancelled at
    process shutdown by the runtime. Each tick's failure is logged +
    swallowed so a transient DB hiccup doesn't kill the scheduler.
    """
    interval = _tick_interval_seconds()
    logger.info("connector sync_loop started (tick=%.1fs)", interval)
    while True:
        try:
            await _tick_once()
        except Exception as exc:  # noqa: BLE001
            logger.warning("connector sync_loop: outer guard caught: %s", exc)
        await asyncio.sleep(interval)
