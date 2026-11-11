"""Bounded API-side relay of persisted events to local socket connections."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


async def event_relay_loop() -> None:
    """Poll bounded delivery pages; enqueue without waiting for socket sends."""
    from app.services.change_event import BROADCAST_SKIP_ACTIONS
    from app.services.change_event_logger import (
        envelope_from_dblog,
        get_change_event_logger,
    )
    from app.services.event_subscription_registry import queue_change_event

    log = get_change_event_logger()
    # Old events remain available through authorized polling. Live relay starts
    # at boot; persisted delivery order prevents delayed facts falling behind it.
    position = datetime.now(timezone.utc).isoformat()
    event_id = ""
    while True:
        try:
            rows = await log.find_after_checkpoint(
                last_logged_at=position, last_event_id=event_id, limit=100
            )
            for row in rows:
                event = envelope_from_dblog(row)
                if event.action not in BROADCAST_SKIP_ACTIONS:
                    queue_change_event(event)
                position, event_id = event.delivery_ts or event.ts, event.id
        except Exception:
            logger.warning("Event relay will retry", exc_info=False)
        await asyncio.sleep(0.5)
