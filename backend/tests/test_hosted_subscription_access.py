"""Unit tests for hosted subscription access (grace + access_until)."""

from datetime import datetime, timezone
from types import SimpleNamespace

from app.services.hosted_subscription import (
    access_for,
    plan_label_for_key,
    plan_summary_for_row,
)


def test_plan_label_for_key():
    assert plan_label_for_key(None) == "Free"
    assert plan_label_for_key("basic") == "Basic"
    assert plan_label_for_key("base") == "Basic"
    assert plan_label_for_key("premium") == "Premium"


def test_plan_summary_missing_is_free():
    assert plan_summary_for_row(None) == {
        "plan_key": "free",
        "plan_label": "Free",
        "subscription_status": None,
        "cancel_at_period_end": False,
    }


def test_plan_summary_active_basic():
    row = SimpleNamespace(
        status="active",
        plan_key="basic",
        cancel_at_period_end=False,
    )
    assert plan_summary_for_row(row)["plan_key"] == "basic"
    assert plan_summary_for_row(row)["plan_label"] == "Basic"


def test_plan_summary_canceled_is_free():
    row = SimpleNamespace(
        status="canceled",
        plan_key="premium",
        cancel_at_period_end=False,
    )
    summary = plan_summary_for_row(row)
    assert summary["plan_key"] == "free"
    assert summary["plan_label"] == "Free"
    assert summary["subscription_status"] == "canceled"


def test_plan_summary_cancel_pending():
    row = SimpleNamespace(
        status="active",
        plan_key="premium",
        cancel_at_period_end=True,
    )
    summary = plan_summary_for_row(row)
    assert summary["plan_key"] == "premium"
    assert summary["cancel_at_period_end"] is True


def test_access_until_locks_after_expiry():
    now = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
    assert (
        access_for(
            "active",
            None,
            now=now,
            access_until="2026-09-29T11:00:00+00:00",
        )
        == "locked"
    )
    assert (
        access_for(
            "active",
            None,
            now=now,
            access_until="2026-09-29T13:00:00+00:00",
        )
        == "open"
    )


def test_access_until_none_defers_to_status():
    now = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
    assert access_for("active", None, now=now, access_until=None) == "open"
    assert access_for("canceled", None, now=now, access_until=None) == "locked"
