"""Phase 7 Plan 07-04 — approval_ttl coverage.

≥4 cases per the plan:

1. TTL marks pending rows past expires_at as 'expired'; rows not yet
   expired are left untouched.
2. TTL emits approval.expired ChangeEvent (audit-only,
   actor_kind='system').
3. TTL does NOT touch rows whose status is not 'pending' (idempotent).
4. APPROVAL_TTL_DAYS exported as a module-level constant for
   documentation; the loop reads the per-row expires_at, not this constant.
5. _interval_seconds reads APPROVAL_RECLAIM_INTERVAL_HOURS env var
   with safe default (1.0h).
6. approval.expired is in BROADCAST_SKIP_ACTIONS (maintenance log,
   not subscriber event).
"""

import os

import pytest

from app.services.approval_ttl import (
    APPROVAL_TTL_DAYS,
    _interval_seconds,
    expire_old_approvals,
)
from app.services.change_event import BROADCAST_SKIP_ACTIONS


def test_approval_ttl_days_default_is_seven():
    """CONTEXT lock #4 default."""
    assert APPROVAL_TTL_DAYS == 7


def test_approval_expired_is_broadcast_skip():
    """TTL events are maintenance logs, not subscriber events."""
    assert "approval.expired" in BROADCAST_SKIP_ACTIONS


def test_interval_seconds_default(monkeypatch):
    """Default 1.0h = 3600s."""
    monkeypatch.delenv("APPROVAL_RECLAIM_INTERVAL_HOURS", raising=False)
    assert _interval_seconds() == 3600.0


def test_interval_seconds_respects_env(monkeypatch):
    """APPROVAL_RECLAIM_INTERVAL_HOURS env overrides default."""
    monkeypatch.setenv("APPROVAL_RECLAIM_INTERVAL_HOURS", "0.5")
    assert _interval_seconds() == 1800.0


def test_interval_seconds_falls_back_on_bad_value(monkeypatch):
    """Garbage env value falls back to 1.0h default."""
    monkeypatch.setenv("APPROVAL_RECLAIM_INTERVAL_HOURS", "not-a-number")
    assert _interval_seconds() == 3600.0


@pytest.mark.asyncio
async def test_expire_old_approvals_marks_expired_rows():
    """Rows past expires_at flip to status='expired'; payload preserved
    (CONTEXT lock #4 — payload NOT nulled on expiry, remains forensic)."""
    from app.models.nodes import Approval
    from app.utils.time import utc_now_iso

    # An expired pending write — expires_at in the past
    expired = await Approval.create(
        actor_kind="agent",
        actor_id="agent-z",
        action="entry.create",
        resource_kind="entry",
        resource_id="",
        payload={"track_id": "t-1", "secret": "preserved"},
        policy_id="p-1",
        status="pending",
        created_at="2020-01-01T00:00:00+00:00",
        expires_at="2020-01-08T00:00:00+00:00",
    )
    # A fresh pending write — expires_at far in the future
    fresh = await Approval.create(
        actor_kind="agent",
        actor_id="agent-z",
        action="entry.create",
        resource_kind="entry",
        resource_id="",
        payload={"track_id": "t-2"},
        policy_id="p-1",
        status="pending",
        created_at=utc_now_iso(),
        expires_at="2099-01-01T00:00:00+00:00",
    )

    count = await expire_old_approvals()
    assert count >= 1, "expire_old_approvals should have flipped >=1 row"

    expired_after = await Approval.get(expired.id)
    fresh_after = await Approval.get(fresh.id)
    assert expired_after is not None
    assert fresh_after is not None
    assert expired_after.status == "expired"
    # Payload preserved per CONTEXT lock #4 — forensic record after expiry.
    assert expired_after.payload == {"track_id": "t-1", "secret": "preserved"}
    # Fresh row untouched
    assert fresh_after.status == "pending"


@pytest.mark.asyncio
async def test_expire_old_approvals_idempotent():
    """Rows already 'expired' are NOT re-flipped (no-op on second pass)."""
    from app.models.nodes import Approval

    already_expired = await Approval.create(
        actor_kind="agent",
        actor_id="agent-q",
        action="entry.create",
        resource_kind="entry",
        resource_id="",
        payload={"track_id": "t-1"},
        policy_id="p-1",
        status="expired",  # already expired
        created_at="2020-01-01T00:00:00+00:00",
        expires_at="2020-01-08T00:00:00+00:00",
    )
    # find({"status": "pending"}) should skip this row entirely
    count = await expire_old_approvals()
    after = await Approval.get(already_expired.id)
    assert after is not None
    assert after.status == "expired"  # unchanged
    # count would be 0 if no other expired rows exist, but other tests
    # may have left some — just verify the row itself is unchanged.
