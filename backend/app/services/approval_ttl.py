"""Phase 7 Plan 07-04 — TTL reclaim loop for Approval rows.

Mirrors ``backend/app/services/change_event_ttl.ttl_reclaim_loop`` VERBATIM.
Same shape:

    async def approval_ttl_loop() -> None:
        interval = _interval_seconds()
        while True:
            try:
                await expire_old_approvals()
            except Exception as e:
                logger.warning("approval_ttl: cycle failed: %s", e)
            await asyncio.sleep(interval)

TESTING gate inherited from ``_startup`` top in ``app/main.py`` — does
NOT spawn under PYTEST_CURRENT_TEST or TESTING=1. Spawned from
``app/main.py`` next to ``ttl_reclaim_loop`` via ``asyncio.create_task``.

Env vars:
    APPROVAL_TTL_DAYS=7                   (used by policy_engine intercept
                                            when stamping expires_at — this
                                            module reads the row's expires_at
                                            field, not the env var)
    APPROVAL_RECLAIM_INTERVAL_HOURS=1.0   (loop wake interval)

``payload`` is NOT nulled on expiry — approvals are short-lived (TTL 7
days default) and the payload remains forensic record even after expiry.
Only ``status`` flips to ``'expired'`` and an ``approval.expired``
ChangeEvent is emitted.

The approval.expired ChangeEvent is in BROADCAST_SKIP_ACTIONS
(change_event.py) — TTL is a maintenance log, not a subscriber event.
The approval review UI polls GET /api/approvals; expiry visibility is
driven by row status on subsequent reads.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

from app.models.nodes import Approval
from app.services.change_event import emit_change_event
from app.utils.time import utc_now

logger = logging.getLogger(__name__)


# Default TTL — used by policy_engine.evaluate when stamping
# Approval.expires_at. Exported here for documentation; the loop itself
# reads the per-row expires_at, not this constant.
APPROVAL_TTL_DAYS = 7


def _interval_seconds() -> float:
    """Wake interval for the TTL loop in seconds (default 1.0h)."""
    try:
        hours = float(os.getenv("APPROVAL_RECLAIM_INTERVAL_HOURS", "1.0"))
    except ValueError:
        hours = 1.0
    return hours * 3600.0


async def expire_old_approvals() -> int:
    """Mark any pending Approval past its ``expires_at`` as expired.

    Idempotent — rows already in non-pending status are skipped.

    Returns the number of rows expired in this pass.
    """
    candidates = await Approval.find({"status": "pending"})
    now = utc_now()
    expired = 0

    for ap in candidates:
        if ap.status != "pending":
            continue  # defensive — find() filter might be stale
        if not ap.expires_at:
            continue  # defensive — row without expires_at is malformed
        try:
            row_expires = datetime.fromisoformat(ap.expires_at)
        except ValueError:
            logger.warning(
                "approval_ttl: unparseable expires_at on approval=%s — skipping",
                ap.id,
            )
            continue
        if row_expires.tzinfo is None:
            row_expires = row_expires.replace(tzinfo=timezone.utc)
        if row_expires > now:
            continue

        ap.status = "expired"
        await ap.save()

        try:
            await emit_change_event(
                actor_kind="system",
                actor_id="approval_ttl",
                action="approval.expired",
                resource_type="approval",
                resource_id=ap.id,
                before=None,
                after=None,
                scope=f"approval:{ap.id}",
                details={
                    "original_action": ap.action,
                    "agent_id": ap.actor_id,
                    "policy_id": ap.policy_id,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "approval_ttl: emit_change_event failed for approval=%s: %s",
                ap.id,
                exc,
            )
        expired += 1

    if expired:
        logger.info("approval_ttl: marked %d approvals expired", expired)
    return expired


async def approval_ttl_loop() -> None:
    """Background loop — wakes every interval, calls expire_old_approvals."""
    interval = _interval_seconds()
    logger.info("approval_ttl: loop started (interval=%.1fs)", interval)
    while True:
        try:
            await expire_old_approvals()
        except Exception as e:  # noqa: BLE001
            logger.warning("approval_ttl: cycle failed: %s", e)
        await asyncio.sleep(interval)
