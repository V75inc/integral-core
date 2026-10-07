"""Phase 5 Plan 05-03 — asyncio sync_loop background scheduler.

Mirrors Phase 2's ``ttl_reclaim_loop`` precedent at
``app/services/change_event_ttl.py:115-124``. Spawned at startup from
``app/main.py`` next to the TTL loop, inside the SAME TESTING gate. The
function-scope ``PYTEST_CURRENT_TEST`` / ``TESTING`` guard at the top of
``_startup`` short-circuits BEFORE the spawn site is reached, so test runs
never accidentally hit external APIs (Pitfall 6 — T-05-03-05 mitigation).

Connectors use the always-on agentive layer. An empty connector registry or
an unknown subclass is a no-op; there is no AGENTIVE_ENABLED boot switch.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)
_running: dict[str, asyncio.Task] = {}


def _observe_sync(connector_id: str, task: asyncio.Task) -> None:
    if _running.get(connector_id) is task:
        _running.pop(connector_id, None)
    exc = None if task.cancelled() else task.exception()
    if exc is not None:
        logger.warning(
            "connector scheduled sync failed: %s",
            connector_id,
            exc_info=(type(exc), exc, exc.__traceback__),
        )


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
        parsed = datetime.fromisoformat(value)
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except (ValueError, TypeError):
        return None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def _maybe_dispatch_one(connector) -> bool:
    """Returns True iff this tick dispatched a sync for ``connector``.

    A connector is eligible when EITHER (a) it has never been synced
    (``last_synced_at is None``) OR (b) the elapsed seconds since the last
    sync >= its configured ``sync_interval_seconds``. Dispatch is
    supervised — each connector's pull is its own asyncio task so a
    slow external API doesn't block the loop tick.
    """
    last = _parse_iso(getattr(connector, "last_synced_at", None))
    previous = _running.get(str(connector.id))
    if previous is not None and not previous.done():
        return False
    interval = float(getattr(connector, "sync_interval_seconds", 300) or 300)
    now = _utc_now()
    if last is not None and (now - last).total_seconds() < interval:
        return False
    # Lazy import keeps module load cheap — sync_runtime pulls in heavy
    # change-event / provenance machinery that only matters at dispatch time.
    from app.services.connectors.sync_runtime import sync_one_connector

    task = asyncio.create_task(
        sync_one_connector(connector), name=f"connector-scheduled:{connector.id}"
    )
    _running[str(connector.id)] = task
    task.add_done_callback(lambda done: _observe_sync(str(connector.id), done))
    return True


async def _tick_once() -> int:
    """Single sync_loop tick body — iterate Connectors, dispatch eligible ones.

    Returns the number of dispatches fired this tick (useful for tests).
    Failures during enumeration are logged + swallowed; the next tick will
    re-attempt. Failures during a per-connector dispatch are observed and logged by
    its completion callback — each ``sync_one_connector`` call manages its
    own ``connector.sync.failed`` emit.
    """
    dispatched = 0
    try:
        # Connector types are available through the always-on agentive layer.
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
    try:
        while True:
            try:
                await _tick_once()
            except Exception as exc:  # noqa: BLE001
                logger.warning("connector sync_loop: outer guard caught: %s", exc)
            await asyncio.sleep(interval)
    finally:
        tasks = list(_running.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
