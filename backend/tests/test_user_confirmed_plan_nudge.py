"""Confirmation nudge: go-ahead with no pending card must steer propose."""

from app.agentive.services.approval_intent import looks_like_approval, looks_like_bless


def test_go_ahead_looks_like_approval():
    assert looks_like_approval("Go ahead")
    assert looks_like_approval("yes")
    assert looks_like_approval("do it")
    assert not looks_like_approval("add more dummy cars please")


def test_rejection_language_cannot_trigger_a_write_nudge():
    request = "Please propose a complete design only; do not build anything yet."

    # The broad parser may detect a rejection word because its job is to
    # route a pending token to reject.  The no-pending write nudge must only
    # use the positive predicate.
    assert looks_like_approval(request)
    assert not looks_like_bless(request)
    assert looks_like_bless("Confirmed. Go ahead and build it.")
