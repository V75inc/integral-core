"""Hosted base-plan access and which writes the lock inspects."""

from datetime import datetime, timedelta, timezone

from app.middleware.billing_lock import (
    request_requires_subscription,
    workspace_id_from_scope_header,
)
from app.services.hosted_subscription import access_for, grace_until_iso


def test_live_statuses_are_open():
    """Trialing and active base plans allow writes."""
    assert access_for("trialing", None) == "open"
    assert access_for("active", None) == "open"


def test_missing_subscription_is_locked():
    """A missing, canceled, or incomplete plan does not allow writes."""
    assert access_for(None, None) == "locked"
    assert access_for("canceled", None) == "locked"
    assert access_for("incomplete", None) == "locked"


def test_past_due_is_grace_for_seven_days_then_locked():
    """Past due stays open for the grace window, then locks."""
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    inside = start + timedelta(days=6)
    outside = start + timedelta(days=8)
    since = start.isoformat()
    assert access_for("past_due", since, now=inside, grace_days=7) == "grace"
    on_boundary = start + timedelta(days=7)
    assert access_for("past_due", since, now=on_boundary, grace_days=7) == "grace"
    assert access_for("past_due", since, now=outside, grace_days=7) == "locked"
    assert (
        grace_until_iso(since, grace_days=7) == (start + timedelta(days=7)).isoformat()
    )


def test_reads_and_billing_routes_skip_the_lock():
    """Reads, billing, auth, and workspace create are not subscription-gated."""
    assert request_requires_subscription("GET", "/api/entries") is False
    assert request_requires_subscription("POST", "/api/billing/checkout") is False
    assert request_requires_subscription("POST", "/api/billing/webhook") is False
    assert request_requires_subscription("POST", "/api/auth/login") is False
    assert request_requires_subscription("POST", "/api/entitlements/grant") is False
    assert request_requires_subscription("POST", "/api/workspaces") is False


def test_content_writes_require_a_subscription():
    """Entry, track, and App install writes are subscription-gated."""
    assert request_requires_subscription("POST", "/api/entries") is True
    assert request_requires_subscription("POST", "/api/apps/batch-install") is True
    assert request_requires_subscription("PATCH", "/api/tracks/n.Track.1") is True
    assert request_requires_subscription("DELETE", "/api/apps/n.App.1") is True


def test_scope_header_parses_workspace_id():
    """Only the ws: prefix yields a workspace id."""
    assert workspace_id_from_scope_header("ws:n.Workspace.9") == "n.Workspace.9"
    assert workspace_id_from_scope_header("n.Workspace.9") == ""
    assert workspace_id_from_scope_header("") == ""


def test_subscription_list_row_includes_access_and_grace():
    """Admin list rows expose access + grace_until for the console table."""
    from app.schemas.billing import HostedSubscriptionResponse
    from app.services.hosted_subscription import access_for_row

    start = datetime.now(timezone.utc) - timedelta(days=1)
    row = type(
        "Row",
        (),
        {
            "workspace_id": "n.Workspace.1",
            "billing_account_id": "ba:n.Workspace.1",
            "status": "past_due",
            "plan_key": "base",
            "source": "stripe",
            "external_customer_id": "cus_x",
            "external_subscription_id": "sub_x",
            "past_due_since": start.isoformat(),
            "created_at": start.isoformat(),
            "updated_at": start.isoformat(),
        },
    )()
    payload = HostedSubscriptionResponse(
        workspace_id=row.workspace_id,
        billing_account_id=row.billing_account_id,
        status=row.status,
        plan_key=row.plan_key,
        source=row.source,
        external_customer_id=row.external_customer_id,
        external_subscription_id=row.external_subscription_id,
        past_due_since=row.past_due_since,
        access=access_for_row(row),
        grace_until=grace_until_iso(row.past_due_since),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
    assert payload.workspace_id == "n.Workspace.1"
    assert payload.access == "grace"
    assert payload.grace_until == grace_until_iso(start.isoformat(), grace_days=7)
    assert payload.source == "stripe"
