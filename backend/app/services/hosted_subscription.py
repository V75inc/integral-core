"""Hosted base-plan projection and the grace lock. No Stripe SDK."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from app.config import settings
from app.exceptions import BadRequestError
from app.models.hosted_subscription import HostedSubscription
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)

_LIVE = frozenset({"trialing", "active"})
_KNOWN = frozenset({"trialing", "active", "past_due", "canceled", "incomplete"})

# Business registers a coroutine that refetches Stripe. Core calls it from
# the hourly loop when the hosted flag is on. Absent in open-source boots.
_stripe_reconcile = None


def register_stripe_reconcile(fn) -> None:
    """Register the Business Stripe refetch. Core never imports that package."""
    global _stripe_reconcile
    _stripe_reconcile = fn


def get_stripe_reconcile():
    """Return the Business Stripe refetch, if one was registered."""
    return _stripe_reconcile


def _parse_iso(value: str) -> datetime:
    text = (value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def access_for(
    status: Optional[str],
    past_due_since: Optional[str],
    *,
    now: Optional[datetime] = None,
    grace_days: Optional[int] = None,
) -> str:
    """Return ``open``, ``grace``, or ``locked`` for one subscription row.

    A missing row is locked. ``past_due`` stays open (as ``grace``) until
    ``BILLING_GRACE_DAYS`` after ``past_due_since``.
    """
    name = (status or "").strip().lower()
    if name in _LIVE:
        return "open"
    if name == "past_due":
        days = settings.BILLING_GRACE_DAYS if grace_days is None else grace_days
        moment = now or datetime.now(timezone.utc)
        if not (past_due_since or "").strip():
            return "grace"
        try:
            started = _parse_iso(past_due_since or "")
        except ValueError:
            return "grace"
        if moment <= started + timedelta(days=days):
            return "grace"
        return "locked"
    return "locked"


def grace_until_iso(
    past_due_since: Optional[str],
    *,
    grace_days: Optional[int] = None,
) -> Optional[str]:
    """ISO time when a past_due plan leaves grace, or None when unset."""
    if not (past_due_since or "").strip():
        return None
    days = settings.BILLING_GRACE_DAYS if grace_days is None else grace_days
    try:
        started = _parse_iso(past_due_since or "")
    except ValueError:
        return None
    return (started + timedelta(days=days)).isoformat()


def access_for_row(row: Optional[HostedSubscription]) -> str:
    """Access for a stored row. A missing row is locked."""
    if row is None:
        return "locked"
    return access_for(row.status, row.past_due_since)


async def find_hosted_subscription(workspace_id: str) -> Optional[HostedSubscription]:
    """Return the workspace base-plan row, if one exists."""
    ws = (workspace_id or "").strip()
    if not ws:
        return None
    rows = await HostedSubscription.find({"context.workspace_id": ws})
    return rows[0] if rows else None


async def upsert_hosted_subscription(
    *,
    workspace_id: str,
    status: str,
    billing_account_id: str = "",
    plan_key: str = "base",
    source: str = "manual",
    external_customer_id: str = "",
    external_subscription_id: str = "",
    past_due_since: Optional[str] = None,
    from_stripe: bool = False,
) -> Tuple[HostedSubscription, bool]:
    """Create or update the projection.

    Returns ``(row, applied)``. Stripe reconcile does not overwrite a
    ``source=manual`` row; ``applied`` is False in that case.
    """
    ws = (workspace_id or "").strip()
    name = (status or "").strip().lower()
    if not ws:
        raise BadRequestError(message="workspace_id is required")
    if name not in _KNOWN:
        raise BadRequestError(
            message=f"unsupported subscription status {status!r}",
            details={"status": status},
        )
    existing = await find_hosted_subscription(ws)
    if from_stripe and existing is not None and (existing.source or "") == "manual":
        return existing, False

    now = utc_now_iso()
    if name == "past_due":
        if past_due_since:
            due = past_due_since
        elif (
            existing is not None
            and (existing.status or "") == "past_due"
            and existing.past_due_since
        ):
            due = existing.past_due_since
        else:
            due = now
    else:
        due = None

    if existing is None:
        row = await HostedSubscription.create(
            workspace_id=ws,
            billing_account_id=(billing_account_id or "").strip(),
            status=name,
            plan_key=(plan_key or "base").strip() or "base",
            source=(source or "manual").strip() or "manual",
            external_customer_id=(external_customer_id or "").strip(),
            external_subscription_id=(external_subscription_id or "").strip(),
            past_due_since=due,
            created_at=now,
            updated_at=now,
        )
        return row, True

    existing.billing_account_id = (
        billing_account_id or existing.billing_account_id or ""
    ).strip()
    existing.status = name
    existing.plan_key = (plan_key or existing.plan_key or "base").strip() or "base"
    existing.source = (source or existing.source or "manual").strip() or "manual"
    if external_customer_id:
        existing.external_customer_id = external_customer_id.strip()
    if external_subscription_id:
        existing.external_subscription_id = external_subscription_id.strip()
    existing.past_due_since = due
    existing.updated_at = now
    await existing.save()
    return existing, True


async def workspace_allows_hosted_writes(workspace_id: str) -> bool:
    """True when the hosted base plan is open or still inside grace."""
    row = await find_hosted_subscription(workspace_id)
    access = access_for_row(row)
    if access == "locked":
        await pause_lapsed_stripe_entitlements(workspace_id)
        return False
    return True


async def pause_lapsed_stripe_entitlements(workspace_id: str) -> None:
    """Revoke Stripe-sourced App entitlements after the base plan lapses.

    Manual grants are left alone.
    """
    from app.services.entitlements import list_entitlements, revoke_stripe_entitlement

    ws = (workspace_id or "").strip()
    if not ws:
        return
    try:
        rows = await list_entitlements(workspace_id=ws)
    except Exception:  # noqa: BLE001
        logger.exception("list entitlements failed during billing lapse ws=%s", ws)
        return
    for row in rows:
        if (row.source or "") != "stripe":
            continue
        if (row.status or "").strip().lower() != "active":
            continue
        try:
            await revoke_stripe_entitlement(
                workspace_id=ws,
                entitlement_key=row.entitlement_key,
                actor_id="system:billing",
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "stripe entitlement revoke failed ws=%s key=%s",
                ws,
                row.entitlement_key,
            )


async def enforce_lapsed_hosted_subscriptions() -> int:
    """Pause Stripe add-ons on every hosted workspace whose base plan is locked."""
    if not settings.INTEGRAL_SUBSCRIPTION_REQUIRED:
        return 0
    paused = 0
    for status in ("past_due", "canceled", "incomplete"):
        rows = await HostedSubscription.find({"context.status": status})
        for row in rows:
            if access_for_row(row) != "locked":
                continue
            await pause_lapsed_stripe_entitlements(row.workspace_id)
            paused += 1
    return paused


async def hosted_billing_reconcile_loop() -> None:
    """Hourly grace enforcement, plus the Business Stripe refetch when registered."""
    import asyncio

    while True:
        try:
            await enforce_lapsed_hosted_subscriptions()
            hook = get_stripe_reconcile()
            if hook is not None:
                await hook()
        except Exception:  # noqa: BLE001
            logger.exception("hosted billing reconcile failed")
        await asyncio.sleep(3600)
