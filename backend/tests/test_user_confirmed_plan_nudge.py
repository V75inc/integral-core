"""Confirmation nudge: go-ahead with no pending card must steer propose."""

from app.agentive.services.approval_intent import looks_like_approval


def test_go_ahead_looks_like_approval():
    assert looks_like_approval("Go ahead")
    assert looks_like_approval("yes")
    assert looks_like_approval("do it")
    assert not looks_like_approval("add more dummy cars please")
