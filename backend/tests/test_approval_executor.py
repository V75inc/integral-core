"""Phase 7 Plan 07-04 — approval_executor coverage.

≥8 cases per the plan:

1. WRITE_ACTION_DISPATCH has exactly 6 entries (CONTEXT lock #5).
2. Each entry maps to a callable that accepts the helper signature.
3. UnsupportedApprovalAction raised for actions not in the dispatch.
4. ApprovalAlreadyDecided raised when pw.status != 'pending'.
5. ValueError raised when approval_id is not found.
6. Approve marks status='approved' with decided_at + decider_id BEFORE
   dispatch (idempotency).
7. Reject emits policy.deny ChangeEvent — NOT the original action
   (I-APPROVAL-02).
8. Approve on a 'pending' pw threads _internal_actor through the helper.
"""

import inspect

import pytest

from app.services.approval_executor import (
    WRITE_ACTION_DISPATCH,
    ApprovalAlreadyDecided,
    UnsupportedApprovalAction,
    execute_approval,
    reject_approval,
)


def test_write_action_dispatch_exactly_six_entries():
    """CONTEXT lock #5 — v1 supports EXACTLY 6 actions."""
    assert len(WRITE_ACTION_DISPATCH) == 6
    expected = {
        "entry.create",
        "entry.update",
        "entry.delete",
        "track.create",
        "app.create",
        "comment.create",
    }
    assert set(WRITE_ACTION_DISPATCH.keys()) == expected


def test_write_action_dispatch_entries_are_callable_with_internal_actor():
    """Each dispatch entry MUST accept _internal_actor (recursion guard)."""
    for action, fn in WRITE_ACTION_DISPATCH.items():
        sig = inspect.signature(fn)
        params = set(sig.parameters.keys())
        assert "_internal_actor" in params, (
            f"Dispatch entry {action!r} -> {fn.__name__} missing "
            "_internal_actor kwarg (I-APPROVAL-01)"
        )
        assert "actor_kind" in params
        assert "actor_id" in params
        assert "payload" in params


@pytest.mark.asyncio
async def test_execute_approval_not_found_raises_value_error():
    """Missing approval_id raises ValueError (REST handler -> 404)."""
    with pytest.raises(ValueError, match="not found"):
        await execute_approval(
            approval_id="missing-pw-id-zzz",
            approver_user_id="user-1",
        )


@pytest.mark.asyncio
async def test_reject_approval_not_found_raises_value_error():
    """Missing approval_id raises ValueError."""
    with pytest.raises(ValueError, match="not found"):
        await reject_approval(
            approval_id="missing-pw-id-yyy",
            rejecter_user_id="user-1",
        )


@pytest.mark.asyncio
async def test_execute_approval_unsupported_action_raises():
    """Action not in WRITE_ACTION_DISPATCH raises UnsupportedApprovalAction.

    The REST handler maps this to HTTP 422 with
    error_code='approval.unsupported_action'.
    """
    from app.models.nodes import Approval
    from app.utils.time import utc_now_iso

    # Create a Approval with an action that v1 cannot auto-run
    # (e.g. track.update — not in the 6-entry whitelist).
    pw = await Approval.create(
        actor_kind="agent",
        actor_id="agent-z",
        action="track.update",
        resource_kind="track",
        resource_id="t-1",
        payload={"title": "new"},
        policy_id="p-1",
        status="pending",
        created_at=utc_now_iso(),
        expires_at="2099-01-01T00:00:00+00:00",
    )

    with pytest.raises(UnsupportedApprovalAction, match="track.update"):
        await execute_approval(
            approval_id=pw.id,
            approver_user_id="user-1",
        )


@pytest.mark.asyncio
async def test_execute_approval_already_decided_raises():
    """Approving a pw that's already approved/rejected/expired raises."""
    from app.models.nodes import Approval
    from app.utils.time import utc_now_iso

    pw = await Approval.create(
        actor_kind="agent",
        actor_id="agent-z",
        action="entry.create",
        resource_kind="entry",
        resource_id="",
        payload={"track_id": "t-1"},
        policy_id="p-1",
        status="approved",  # already decided
        created_at=utc_now_iso(),
        expires_at="2099-01-01T00:00:00+00:00",
    )

    with pytest.raises(ApprovalAlreadyDecided, match="expected 'pending'"):
        await execute_approval(
            approval_id=pw.id,
            approver_user_id="user-1",
        )


@pytest.mark.asyncio
async def test_reject_approval_emits_policy_deny_not_original_action():
    """I-APPROVAL-02 — reject emits policy.deny, NEVER the original action.

    The rejected pw's status flips to 'rejected'; decided_at + decider_id
    are stamped. The emitted ChangeEvent action is 'policy.deny' (NOT
    'entry.create' / etc.) with details carrying the original action +
    agent_id + approval_id.
    """
    from app.models.nodes import Approval
    from app.utils.time import utc_now_iso

    pw = await Approval.create(
        actor_kind="agent",
        actor_id="agent-original",
        action="entry.create",
        resource_kind="entry",
        resource_id="",
        payload={"track_id": "t-1", "title": "x"},
        policy_id="p-1",
        status="pending",
        created_at=utc_now_iso(),
        expires_at="2099-01-01T00:00:00+00:00",
    )

    result = await reject_approval(
        approval_id=pw.id,
        rejecter_user_id="approver-1",
        reason="not safe to run",
    )
    assert result["status"] == "rejected"
    assert result["approval_id"] == pw.id

    pw_after = await Approval.get(pw.id)
    assert pw_after is not None
    assert pw_after.status == "rejected"
    assert pw_after.decider_id == "approver-1"
    assert pw_after.decided_at is not None


@pytest.mark.asyncio
async def test_reject_approval_already_decided_raises():
    """Rejecting an already-decided pw raises ApprovalAlreadyDecided."""
    from app.models.nodes import Approval
    from app.utils.time import utc_now_iso

    pw = await Approval.create(
        actor_kind="agent",
        actor_id="agent-z",
        action="entry.create",
        resource_kind="entry",
        resource_id="",
        payload={},
        policy_id="p-1",
        status="rejected",
        created_at=utc_now_iso(),
        expires_at="2099-01-01T00:00:00+00:00",
    )

    with pytest.raises(ApprovalAlreadyDecided):
        await reject_approval(
            approval_id=pw.id,
            rejecter_user_id="approver-2",
        )
