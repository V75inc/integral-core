"""Unit tests for hosted subscription access (grace + access_until)."""

from datetime import datetime, timezone

from app.services.hosted_subscription import access_for


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
