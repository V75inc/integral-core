"""Phase 9 Plan 09-02 (NOTIF-01) — mark_read / mark_all_read backend tests.

D-05 single-emission invariant for two notification state-of-read transitions:
``notification.read`` (per-notification) and ``notification.mark_all_read``
(per-batch single emission). PolicyAction strict-superset preserved.

The test surface covers five concerns:

1. ChangeEventAction Literal includes the two additive members.
2. PolicyAction Literal includes the two additive members (strict-superset).
3. ``PUT /api/notifications/{id}/read`` flips ``read=True`` AND emits exactly
   one ``notification.read`` ChangeEvent.
4. ``PUT /api/notifications/mark-all-read`` flips every unread notification AND
   emits exactly one ``notification.mark_all_read`` ChangeEvent carrying
   ``details.count`` + ``details.notification_ids``.
5. Cross-user mark_read returns 403 (existing ownership gate; unchanged).

Additionally: the D-05 bypass scanner must NOT report
``mark_notification_as_read`` or ``mark_all_notifications_as_read`` as missing
``emit_change_event`` calls. Other deferred D-05 gaps may remain (out of
scope for this plan).
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import get_args

import pytest

from app.models.nodes import Notification
from app.schemas.audit import ChangeEventAction
from app.schemas.policy import PolicyAction

# ---------------------------------------------------------------------------
# 1+2 — Literal membership + strict-superset invariant (I-CHA).
# ---------------------------------------------------------------------------


def test_change_event_action_includes_notification_read_members():
    """ChangeEventAction Literal MUST include both new members (additive)."""
    members = set(get_args(ChangeEventAction))
    assert "notification.read" in members
    assert "notification.mark_all_read" in members


def test_policy_action_includes_notification_read_members():
    """PolicyAction mirrors the two new ChangeEventAction members."""
    members = set(get_args(PolicyAction))
    assert "notification.read" in members
    assert "notification.mark_all_read" in members


def test_policy_action_strict_supersets_change_event_action_for_notif_members():
    """The two new ChangeEventAction members must be a subset of PolicyAction.

    Mirrors docs/INVARIANTS.md "PolicyAction strict-supersets ChangeEventAction"
    invariant; scoped here to the two new members so this plan does not
    regress on pre-existing audit-only exemptions (policy.deny, anchor.deny,
    approval.expired).
    """
    cea = set(get_args(ChangeEventAction))
    pa = set(get_args(PolicyAction))
    new_members = {"notification.read", "notification.mark_all_read"}
    assert new_members.issubset(cea)
    assert new_members.issubset(pa)
    assert new_members.issubset(pa & cea)


# ---------------------------------------------------------------------------
# Bypass-scanner gate — neither mark-read handler is permitted in ALLOW_LIST.
# ---------------------------------------------------------------------------


def test_mark_read_handlers_removed_from_allow_list():
    """Both notifications mark-read handlers MUST be removed from D-05 ALLOW_LIST.

    The two handlers now emit ChangeEvents directly (closing 2 of 43 deferred
    gaps). Their ALLOW_LIST entries in test_change_event_no_bypass.py are
    therefore stale and MUST be removed by this plan.
    """
    from tests.test_change_event_no_bypass import ALLOW_LIST

    assert (
        "backend/app/api/notifications.py",
        "mark_notification_as_read",
    ) not in ALLOW_LIST
    assert (
        "backend/app/api/notifications.py",
        "mark_all_notifications_as_read",
    ) not in ALLOW_LIST


def test_mark_read_handlers_call_emit_change_event():
    """AST scan: both handlers in api/notifications.py call emit_change_event."""
    repo_root = Path(__file__).resolve().parents[2]
    src = (repo_root / "backend" / "app" / "api" / "notifications.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(src)
    found: dict[str, bool] = {
        "mark_notification_as_read": False,
        "mark_all_notifications_as_read": False,
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name in found:
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call):
                    target = inner.func
                    if (
                        isinstance(target, ast.Name)
                        and target.id == "emit_change_event"
                    ):
                        found[node.name] = True
                    elif (
                        isinstance(target, ast.Attribute)
                        and target.attr == "emit_change_event"
                    ):
                        found[node.name] = True
    assert all(found.values()), f"missing emit_change_event in handlers: {found}"


# ---------------------------------------------------------------------------
# 3 — PUT /api/notifications/{id}/read emits ChangeEvent notification.read.
# ---------------------------------------------------------------------------


async def _create_notification_for(user_id: str, *, read: bool = False) -> Notification:
    """Create a Notification directly via the jvspatial Node CRUD path.

    Mirrors api/notifications.py::create_notification but skips the HTTP path
    (and the notification.create ChangeEvent that would otherwise pollute the
    audit log we are asserting against in tests below).
    """
    from datetime import datetime

    return await Notification.create(
        user_id=user_id,
        type="system",
        content="test",
        read=read,
        metadata={},
        created_at=datetime.now().isoformat(),
    )


async def _read_change_events(action: str) -> list[dict]:
    """Read persisted ChangeEvents with the given action from the logging DB.

    Returns wire-shape dicts (via envelope_from_dblog → to_wire_flat) filtered
    by action. The find_all helper does not filter on action — it filters on
    scope / actor_kind — so we walk the full set and apply the action filter
    in-process. Acceptable: per-test the change-event DB is freshly seeded by
    the autouse setup_test_db fixture, so the row count is small.
    """
    from app.services.change_event_logger import (
        envelope_from_dblog,
        get_change_event_logger,
    )

    ce_logger = get_change_event_logger()
    rows = await ce_logger.find_all()
    out: list[dict] = []
    for row in rows:
        try:
            env = envelope_from_dblog(row)
        except Exception:
            continue
        if env.action != action:
            continue
        out.append(env.to_wire_flat())
    return out


@pytest.mark.asyncio
async def test_mark_notification_as_read_flips_flag_and_emits_one_event(
    authenticated_client, test_user
):
    """PUT /notifications/{id}/read flips flag + emits exactly one ChangeEvent."""
    if test_user is None or not getattr(test_user, "user_id", None):
        pytest.skip("test_user fixture unavailable")

    notif = await _create_notification_for(test_user.user_id)
    resp = await authenticated_client.put(f"/api/notifications/{notif.id}/read")
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert body.get("notification", {}).get("read") is True

    # Re-fetch via DB to confirm persistence.
    refreshed = await Notification.get(notif.id)
    assert refreshed is not None and refreshed.read is True

    rows = await _read_change_events("notification.read")
    matching = [r for r in rows if r.get("resource_id") == notif.id]
    assert (
        len(matching) == 1
    ), f"expected 1 notification.read event, got {len(matching)}"
    evt = matching[0]
    assert evt.get("actor_kind") == "human"
    assert evt.get("actor_id") == test_user.user_id
    assert evt.get("scope") == f"user:{test_user.user_id}"
    assert evt.get("before") == {"read": False}
    assert evt.get("after") == {"read": True}


# ---------------------------------------------------------------------------
# 4 — PUT /api/notifications/mark-all-read emits ONE batch ChangeEvent.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_all_read_flips_all_and_emits_one_batch_event(
    authenticated_client, test_user
):
    """PUT /notifications/mark-all-read emits exactly one batch ChangeEvent."""
    if test_user is None or not getattr(test_user, "user_id", None):
        pytest.skip("test_user fixture unavailable")

    n1 = await _create_notification_for(test_user.user_id)
    n2 = await _create_notification_for(test_user.user_id)
    n3 = await _create_notification_for(test_user.user_id, read=True)  # already read

    resp = await authenticated_client.put("/api/notifications/mark-all-read")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("count") == 2  # only 2 unread were flipped

    # Confirm DB state.
    for nid in (n1.id, n2.id, n3.id):
        refreshed = await Notification.get(nid)
        assert refreshed is not None and refreshed.read is True

    rows = await _read_change_events("notification.mark_all_read")
    matching = [r for r in rows if r.get("resource_id") == test_user.user_id]
    assert (
        len(matching) == 1
    ), f"expected 1 notification.mark_all_read event, got {len(matching)}"
    evt = matching[0]
    assert evt.get("actor_kind") == "human"
    assert evt.get("actor_id") == test_user.user_id
    assert evt.get("scope") == f"user:{test_user.user_id}"
    details = evt.get("details") or {}
    assert details.get("count") == 2
    ids = set(details.get("notification_ids") or [])
    assert ids == {n1.id, n2.id}


@pytest.mark.asyncio
async def test_mark_all_read_no_unread_emits_no_event(authenticated_client, test_user):
    """When there are no unread notifications, NO ChangeEvent is emitted."""
    if test_user is None or not getattr(test_user, "user_id", None):
        pytest.skip("test_user fixture unavailable")

    # No notifications at all.
    resp = await authenticated_client.put("/api/notifications/mark-all-read")
    assert resp.status_code == 200, resp.text
    assert resp.json().get("count") == 0

    rows = await _read_change_events("notification.mark_all_read")
    matching = [r for r in rows if r.get("resource_id") == test_user.user_id]
    assert len(matching) == 0


# ---------------------------------------------------------------------------
# 5 — cross-user ownership gate unchanged (mitigates T-09-02-S01).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_user_mark_read_returns_403(
    authenticated_client, test_user, second_user_client, second_user
):
    """second_user cannot mark test_user's notification as read."""
    if test_user is None or not getattr(test_user, "user_id", None):
        pytest.skip("test_user fixture unavailable")
    if second_user is None or not getattr(second_user, "user_id", None):
        pytest.skip("second_user fixture unavailable")

    notif = await _create_notification_for(test_user.user_id)
    resp = await second_user_client.put(f"/api/notifications/{notif.id}/read")
    # InsufficientPermissionsError surfaces as 403 via JVSpatialAPIException.
    assert resp.status_code == 403, resp.text

    # No ChangeEvent for the unauthorized attempt.
    rows = await _read_change_events("notification.read")
    matching = [r for r in rows if r.get("resource_id") == notif.id]
    assert len(matching) == 0
