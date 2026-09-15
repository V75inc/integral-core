"""Phase 7 Plan 07-04 — policy_engine.evaluate requires_human_approval intercept.

Six cases per CONTEXT post-research lock #4 + #6 + #9:

1. Decision schema parses with the new approval_id field.
2. ChangeEventAction Literal includes approval.expired.
3. PolicyAction Literal includes approval.approve + approval.reject.
4. Approval Node importable + persistable with the locked 9-field shape.
5. Intercept fires when matched.requires_human_approval=True AND action=write
   AND subject.kind='agent' → returns Decision(allowed=False,
   reason='requires_human_approval', approval_id=<id>); Approval
   persisted with correct fields.
6. Intercept SKIPS when subject.kind != 'agent' (human caller) — write goes
   through normally (no Approval created).
7. Intercept SKIPS for read actions (entry.read) even when policy has
   requires_human_approval=True.
8. Payload >1MB returns 413 / PayloadTooLargeError; no Approval
   persisted.

Wave-3 RED test — passes after Task 3 lands the intercept.
"""

from typing import get_args

import pytest

from app.models.nodes import Approval
from app.schemas.audit import ChangeEventAction
from app.schemas.policy import Decision, PolicyAction, Resource, Subject


def test_decision_parses_approval_id_field():
    """Decision schema accepts the new additive approval_id field."""
    d = Decision(
        allowed=False,
        reason="requires_human_approval",
        approval_id="pw-1",
    )
    assert d.approval_id == "pw-1"
    assert d.allowed is False
    assert d.reason == "requires_human_approval"


def test_decision_approval_id_defaults_none():
    """Existing Decision call sites unchanged — approval_id defaults to None."""
    d = Decision(allowed=True, reason="policy_match:p-1")
    assert d.approval_id is None


def test_change_event_action_includes_approval_expired():
    """ChangeEventAction Literal gained approval.expired (audit-only)."""
    members = set(get_args(ChangeEventAction))
    assert "approval.expired" in members


def test_policy_action_includes_approval_approve_reject():
    """PolicyAction Literal gained approval.approve + approval.reject."""
    members = set(get_args(PolicyAction))
    assert "approval.approve" in members
    assert "approval.reject" in members


def test_approval_expired_is_audit_only_no_policy_action_twin():
    """I-APPROVAL-03 — approval.expired exists in CEA but NOT in PolicyAction."""
    pa_members = set(get_args(PolicyAction))
    assert "approval.expired" not in pa_members, (
        "approval.expired must remain CEA-only (audit-only TTL emission); "
        "see I-APPROVAL-03 in docs/INVARIANTS.md"
    )


def test_pending_agent_write_node_has_locked_9_field_shape():
    """Approval Node carries the locked CONTEXT #4 shape."""
    pw = Approval(
        actor_kind="agent",
        actor_id="agent-1",
        action="entry.create",
        resource_kind="entry",
        resource_id="",
        payload={"title": "test"},
        policy_id="p-1",
        created_at="2026-05-17T00:00:00+00:00",
        expires_at="2026-05-24T00:00:00+00:00",
        status="pending",
    )
    # Locked 9 fields per CONTEXT post-research lock #4
    assert pw.actor_kind == "agent"
    assert pw.actor_id == "agent-1"
    assert pw.action == "entry.create"
    assert pw.resource_kind == "entry"
    assert pw.resource_id == ""
    assert pw.payload == {"title": "test"}
    assert pw.policy_id == "p-1"
    assert pw.created_at == "2026-05-17T00:00:00+00:00"
    assert pw.expires_at == "2026-05-24T00:00:00+00:00"
    assert pw.status == "pending"
    # + audit timestamps (decided_at, decider_id) default to None
    assert pw.decided_at is None
    assert pw.decider_id is None


@pytest.mark.asyncio
async def test_policy_engine_intercept_fires_for_agent_write_with_requires_human_approval():
    """Intercept fires for agent + write + requires_human_approval=True.

    Returns Decision(allowed=False, reason='requires_human_approval',
    approval_id=<persisted Approval.id>).
    """
    from app.models.nodes import Policy
    from app.services.policy_engine import evaluate
    from app.utils.time import utc_now_iso

    # Create a Policy attached to an agent subject with requires_human_approval=True
    policy = await Policy.create(
        subject_kind="agent",
        subject_id="agent-rha-1",
        scope="*",
        actions=["entry.create"],
        entry_types=[],
        tags=[],
        requires_human_approval=True,
        is_active=True,
        created_at=utc_now_iso(),
    )
    # Attach the Policy to the agent's HAS_POLICY edge surface
    # (handled by the engine's _find_policies_for_subject; for the RED test
    # we simulate via direct edge connection — see Phase 3 Plan 03-03 idiom).
    from app.models.edges import HAS_POLICY
    from app.models.nodes import User

    user_or_agent = await User.create(
        email="agent-rha-1@example.com",
        password_hash="x",
        user_id="agent-rha-1",
    )
    await user_or_agent.connect(policy, edge=HAS_POLICY, attached_at=utc_now_iso())

    decision = await evaluate(
        subject=Subject(kind="agent", id="agent-rha-1"),
        action="entry.create",
        resource=Resource(kind="entry", id="", scope="track:t-1"),
        payload={"title": "test"},
    )

    assert (
        decision.allowed is False
    ), f"Intercept must deny the agent write; got Decision={decision!r}"
    assert (
        decision.reason == "requires_human_approval"
    ), f"Intercept must set reason=requires_human_approval; got {decision.reason!r}"
    assert (
        decision.approval_id is not None
    ), "Intercept must persist a Approval and surface its id"

    # The persisted Approval captures the agent's identity and the
    # original action — NOT the approving human (re-run preserves agent in audit).
    pw = await Approval.get(decision.approval_id)
    assert pw is not None
    assert pw.actor_kind == "agent"
    assert pw.actor_id == "agent-rha-1"
    assert pw.action == "entry.create"
    assert pw.status == "pending"


@pytest.mark.asyncio
async def test_policy_engine_intercept_skips_for_human_caller():
    """Human caller is NEVER intercepted — even when the matched policy has
    requires_human_approval=True (which only applies to agent writes).

    Human writes go through the default-human path (legacy permission helpers).
    """
    from app.services.policy_engine import evaluate

    # Human caller — bypasses the agent-policy branch entirely.
    # If no human permission exists, we'll see default_human_policy_denied;
    # the critical check is that the reason is NOT 'requires_human_approval'.
    decision = await evaluate(
        subject=Subject(kind="human", id="user-1"),
        action="entry.create",
        resource=Resource(kind="entry", id="", scope="track:t-1"),
        payload={"title": "test"},
    )
    assert decision.reason != "requires_human_approval", (
        "Intercept must NOT fire for human callers; got " f"Decision={decision!r}"
    )
    assert decision.approval_id is None


@pytest.mark.asyncio
async def test_policy_engine_intercept_skips_for_read_action():
    """Read actions are NEVER intercepted — even on an agent with
    requires_human_approval=True. Read intercept would deadlock the
    approval workflow itself (the UI lists pending writes via reads).
    """
    from app.models.edges import HAS_POLICY
    from app.models.nodes import Policy, User
    from app.services.policy_engine import evaluate
    from app.utils.time import utc_now_iso

    policy = await Policy.create(
        subject_kind="agent",
        subject_id="agent-read-1",
        scope="*",
        actions=["entry.read"],
        entry_types=[],
        tags=[],
        requires_human_approval=True,
        is_active=True,
        created_at=utc_now_iso(),
    )
    agent = await User.create(
        email="agent-read-1@example.com",
        password_hash="x",
        user_id="agent-read-1",
    )
    await agent.connect(policy, edge=HAS_POLICY, attached_at=utc_now_iso())

    decision = await evaluate(
        subject=Subject(kind="agent", id="agent-read-1"),
        action="entry.read",
        resource=Resource(kind="entry", id="e-1", scope="track:t-1"),
        payload=None,  # reads carry no payload
    )
    # Read either allow (policy matches read action) or deny — but NEVER
    # the requires_human_approval intercept.
    assert decision.reason != "requires_human_approval"
    assert decision.approval_id is None
