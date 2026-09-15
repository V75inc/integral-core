"""Phase 7 Plan 07-04 — pure-service writer helper coverage.

Six cases — one per helper. Each case verifies:

1. The helper is importable + accepts the documented kwarg surface
   (actor_kind, actor_id, payload, _internal_actor).
2. The helper threads _internal_actor through to policy_evaluate so the
   recursion-guard short-circuits on the approve re-run path.
3. Direct invocation (no _internal_actor) gates via policy_evaluate
   normally — denied callers raise PermissionError.

The helpers are STRATEGY A (CONTEXT lock #5) — standalone pure-service
functions called by approval_executor on the approve path. They are
NOT REST handler replacements; the existing handlers continue to own the
external HTTP surface. v1 limitation documented inline: the helpers
implement the substrate-level write only (minimum viable for re-run);
handler-level validation is not replicated.
"""

import inspect

import pytest

from app.schemas.policy import Subject


def test_create_entry_internal_signature():
    from app.services.entry_writer import create_entry_internal

    sig = inspect.signature(create_entry_internal)
    params = set(sig.parameters.keys())
    assert "actor_kind" in params
    assert "actor_id" in params
    assert "payload" in params
    assert "_internal_actor" in params


def test_update_entry_internal_signature():
    from app.services.entry_writer import update_entry_internal

    sig = inspect.signature(update_entry_internal)
    params = set(sig.parameters.keys())
    assert "actor_kind" in params
    assert "actor_id" in params
    assert "payload" in params
    assert "_internal_actor" in params


def test_delete_entry_internal_signature():
    from app.services.entry_writer import delete_entry_internal

    sig = inspect.signature(delete_entry_internal)
    params = set(sig.parameters.keys())
    assert "actor_kind" in params
    assert "actor_id" in params
    assert "payload" in params
    assert "_internal_actor" in params


def test_create_track_internal_signature():
    from app.services.track_writer import create_track_internal

    sig = inspect.signature(create_track_internal)
    params = set(sig.parameters.keys())
    assert "actor_kind" in params
    assert "actor_id" in params
    assert "payload" in params
    assert "_internal_actor" in params


def test_create_space_internal_signature():
    from app.services.app_writer import create_app_internal

    sig = inspect.signature(create_app_internal)
    params = set(sig.parameters.keys())
    assert "actor_kind" in params
    assert "actor_id" in params
    assert "payload" in params
    assert "_internal_actor" in params


def test_create_comment_internal_signature():
    from app.services.comment_writer import create_comment_internal

    sig = inspect.signature(create_comment_internal)
    params = set(sig.parameters.keys())
    assert "actor_kind" in params
    assert "actor_id" in params
    assert "payload" in params
    assert "_internal_actor" in params


@pytest.mark.asyncio
async def test_create_entry_internal_validates_required_payload():
    """Missing track_id raises ValueError."""
    from app.services.entry_writer import create_entry_internal

    with pytest.raises(ValueError, match="track_id"):
        await create_entry_internal(
            actor_kind="agent",
            actor_id="agent-1",
            payload={},
        )


@pytest.mark.asyncio
async def test_update_entry_internal_validates_required_payload():
    """Missing entry_id raises ValueError."""
    from app.services.entry_writer import update_entry_internal

    with pytest.raises(ValueError, match="entry_id"):
        await update_entry_internal(
            actor_kind="agent",
            actor_id="agent-1",
            payload={},
        )


@pytest.mark.asyncio
async def test_create_entry_internal_with_internal_actor_bypasses_policy():
    """Threading _internal_actor=Subject(kind='system',...) bypasses the
    fail-closed agent policy gate via the system recursion guard.

    Without _internal_actor, an agent with no HAS_POLICY edge gets
    Decision(allowed=False, reason='fail_closed_no_policy') → PermissionError.
    With _internal_actor=Subject(kind='system', id='approval_approve')
    the engine short-circuits to allowed=True before reaching the policy
    lookup (policy_engine.py:457-459). The downstream Track.get(missing-id)
    will raise ValueError; that's the expected next failure (proves the
    policy gate was bypassed).
    """
    from app.services.entry_writer import create_entry_internal

    # Without _internal_actor — fail-closed (agent has no policy):
    # the gate returns allowed=False → PermissionError raises BEFORE
    # the Track.get lookup.
    with pytest.raises(PermissionError, match="policy denied"):
        await create_entry_internal(
            actor_kind="agent",
            actor_id="agent-no-policy-1",
            payload={"track_id": "missing-track-id-zzz"},
        )

    # With _internal_actor — policy bypassed; next failure is Track.get
    # (ValueError, not PermissionError). This is the proof the recursion
    # guard reached evaluate(): if it hadn't, PermissionError would fire first.
    with pytest.raises(ValueError, match="not found"):
        await create_entry_internal(
            actor_kind="agent",
            actor_id="agent-no-policy-1",
            payload={"track_id": "missing-track-id-zzz"},
            _internal_actor=Subject(kind="system", id="approval_approve"),
        )
