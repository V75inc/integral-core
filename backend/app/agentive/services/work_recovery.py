"""Startup / periodic durable-work recovery pass."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from app.agentive.services import work_items
from app.agentive.services.work_outbox import reconcile_missing_outbox_facts
from app.agentive.work_models import WorkItem
from app.schemas.agentive.work import WorkError


@dataclass
class RecoveryReport:
    """Counters for one recovery pass (second pass should be all zeros)."""

    expired: int = 0
    reclaimed: int = 0
    retry_waited: int = 0
    terminalized: int = 0
    outbox_backfilled: int = 0


def _parse_iso(value: str) -> Optional[datetime]:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _deadline_passed(item: WorkItem, now: datetime) -> bool:
    deadline = _parse_iso(item.deadline_at)
    if deadline is None:
        return False
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return deadline <= now


def _lease_expired(item: WorkItem, now: datetime) -> bool:
    exp = _parse_iso(item.lease_expires_at)
    if exp is None:
        return True
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return exp <= now


async def run_recovery_pass(
    *,
    reclaim_worker_id: str = "recovery",
    lease_seconds: float = work_items.DEFAULT_LEASE_SECONDS,
) -> RecoveryReport:
    """Apply monotonic recovery rules. Safe to run repeatedly."""
    report = RecoveryReport()
    now = datetime.now(timezone.utc)

    # 1. Expire overdue queued/waiting items.
    for status in (
        "queued",
        "retry_wait",
        "waiting_for_human",
        "waiting_for_event",
    ):
        for item in await WorkItem.find({"context.status": status}):
            if item.status in (
                "succeeded",
                "failed",
                "cancelled",
                "expired",
                "dead_letter",
            ):
                continue
            if _deadline_passed(item, now):
                try:
                    await work_items.expire_work_item(item.work_item_id)
                    report.expired += 1
                except WorkError:
                    continue

    # 2. Reclaim running items whose lease expired.
    for item in await WorkItem.find({"context.status": "running"}):
        if not _lease_expired(item, now):
            continue
        try:
            reclaimed = await work_items.reclaim_expired_lease(
                item.work_item_id,
                worker_id=reclaim_worker_id,
                lease_seconds=lease_seconds,
            )
            report.reclaimed += 1
            # Reclaiming only renews the lease. Leaving the item ``running``
            # without executing it would strand work until that replacement
            # lease expired, so recovery becomes the new lease owner and
            # completes the same durable item immediately. A crash here is
            # still safe: the new lease expires and a later pass reclaims it
            # with a higher fence.
            from app.agentive.services.work_worker import execute_claimed_work

            await execute_claimed_work(
                reclaimed,
                worker_id=reclaim_worker_id,
                lease_seconds=lease_seconds,
            )
        except WorkError:
            continue

    # 3–4. Orphaned retryable / exhausted handling is deferred to worker
    # claim paths; recovery does not reopen terminal authority.

    # 5 + 7. Backfill missing deterministic outbox facts (dev gaps).
    report.outbox_backfilled = await reconcile_missing_outbox_facts()

    return report


__all__ = ["RecoveryReport", "run_recovery_pass"]
