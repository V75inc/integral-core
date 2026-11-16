"""Fast reviewed action-vocabulary contracts for full and PR smoke gates."""

from typing import get_args

import pytest

# Reviewed additions after Phase 8 cover access lifecycle, model
# configuration, routines and governed operations (9251cc2c, 0d5a65aa,
# e0405cb2, 18292f01, e07271cd, 76e502ab, 51e796dc and 56d9c7c1).
POLICY_ACTION_BASELINE = 174
CHANGE_EVENT_ACTION_BASELINE = 146
# A committed-message invalidation is deliberately additive to the frozen
# baseline, not a new Settings verb. Its contract and authority are specified
# in docs/backend/ai-chat.md#committed-message-updates-across-sessions.
REVIEWED_CHAT_ACTIONS = frozenset({"chat.message.create"})


@pytest.mark.smoke
def test_policy_action_unchanged() -> None:
    """PolicyAction Literal must match the reviewed action count.

    A1 of plan 08-06 — Settings panels are READ proxies (I-SET-01), so no
    new ``settings.*`` / ``retrieval.*`` / ``binding.*`` action members
    should have appeared.

    The explicit committed-chat addition is documented in the canonical chat
    contract. Any other addition requires a reviewed contract and baseline
    decision; these frozen counts and the presence check still reject drift.
    """
    from app.schemas.policy import PolicyAction

    members = set(get_args(PolicyAction))
    assert REVIEWED_CHAT_ACTIONS <= members
    assert len(members - REVIEWED_CHAT_ACTIONS) == POLICY_ACTION_BASELINE, (
        f"PolicyAction Literal count changed: expected "
        f"{POLICY_ACTION_BASELINE}, got {len(members)}. "
        f"Only the explicitly documented reviewed additions may extend the baseline. New "
        f"members require a reviewed canonical contract."
    )


@pytest.mark.smoke
def test_change_event_action_unchanged() -> None:
    """ChangeEventAction Literal must match the reviewed audit action count.

    Phase 8 added zero new audit event types — Connector CRUD reuses the
    existing ``connector.create/update/delete`` members, resource-level
    invitation revoke reuses ``workspace.invitation_revoke`` (08-05 §A4).
    """
    from app.schemas.audit import ChangeEventAction

    members = set(get_args(ChangeEventAction))
    assert REVIEWED_CHAT_ACTIONS <= members
    assert len(members - REVIEWED_CHAT_ACTIONS) == CHANGE_EVENT_ACTION_BASELINE, (
        f"ChangeEventAction Literal count changed: expected "
        f"{CHANGE_EVENT_ACTION_BASELINE}, got {len(members)}. "
        f"Only the explicitly documented reviewed additions may extend the baseline. New "
        f"members require a reviewed canonical contract."
    )
