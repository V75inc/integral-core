"""Unit tests for the core event subscription registry (Plan 02-03 Task 1).

Per CONTEXT D-12: registry lives in app/services/ (core, NOT agentive).
Per CONTEXT D-07: every broadcast applies a per-message permission check —
revocation takes effect on the very next broadcast.

These tests use AsyncMock for WebSocket and monkeypatch the can_view_*
permission helpers for deterministic control.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest


def _new_change_event(scope: str, *, action: str = "entry.create") -> Any:
    """Construct a ChangeEventEnvelope in-memory (no persistence) with predictable fields."""
    from app.services.change_event_logger import ChangeEventEnvelope

    return ChangeEventEnvelope(
        id="ce-test-1",
        ts="2026-05-07T00:00:00+00:00",
        actor_kind="human",
        actor_id="actor-1",
        actor_capability=None,
        action=action,
        resource_type="Entry",
        resource_id="e-1",
        scope=scope,
        before=None,
        after={"id": "e-1"},
    )


@pytest.fixture(autouse=True)
def _clear_registry_between_tests():
    """Clear the in-process subscription registry between tests."""
    from app.services import event_subscription_registry as esr

    esr._subscriptions.clear()
    yield
    esr._subscriptions.clear()


@pytest.mark.asyncio
async def test_register_unregister_round_trip():
    from app.services.event_subscription_registry import (
        _subscriptions,
        register_subscription,
        unregister_subscription,
    )

    ws = AsyncMock()
    await register_subscription("user-1", ws)
    assert "user-1" in _subscriptions
    assert _subscriptions["user-1"] == [ws]

    await unregister_subscription("user-1", ws)
    assert "user-1" not in _subscriptions


@pytest.mark.asyncio
async def test_multiple_connections_per_user_all_receive(monkeypatch):
    """Same user with two ws connections (multi-tab) → broadcast reaches both."""
    from app.schemas.policy import Decision as _Decision
    from app.services.event_subscription_registry import (
        broadcast_change_event,
        register_subscription,
    )

    async def always_allow(*args, **kwargs):
        return _Decision(allowed=True, reason="test_bypass")

    # Plan 03-02 migrated the per-message filter to policy_engine.evaluate.
    monkeypatch.setattr(
        "app.services.event_subscription_registry.policy_evaluate", always_allow
    )

    ws_a, ws_b = AsyncMock(), AsyncMock()
    await register_subscription("user-1", ws_a)
    await register_subscription("user-1", ws_b)

    await broadcast_change_event(_new_change_event("track:T1"))

    assert ws_a.send_text.await_count == 1
    assert ws_b.send_text.await_count == 1


@pytest.mark.asyncio
async def test_broadcast_track_scope_permission_filter(monkeypatch):
    """D-07 — track scope: subscribers without can_view_track receive nothing."""
    from app.schemas.policy import Decision as _Decision
    from app.services.event_subscription_registry import (
        broadcast_change_event,
        register_subscription,
    )

    async def fake_evaluate(*, subject, action, resource, _internal_actor=None):
        return _Decision(
            allowed=(subject.id == "user-A"),
            reason="test_filter",
        )

    # Plan 03-02 migrated the per-message filter to policy_engine.evaluate.
    monkeypatch.setattr(
        "app.services.event_subscription_registry.policy_evaluate",
        fake_evaluate,
    )

    ws_a, ws_b = AsyncMock(), AsyncMock()
    await register_subscription("user-A", ws_a)
    await register_subscription("user-B", ws_b)

    await broadcast_change_event(_new_change_event("track:T1"))

    assert ws_a.send_text.await_count == 1
    assert ws_b.send_text.await_count == 0


@pytest.mark.asyncio
async def test_broadcast_space_scope_permission_filter(monkeypatch):
    """D-07 — space scope: subscribers without can_view_space receive nothing."""
    from app.schemas.policy import Decision as _Decision
    from app.services.event_subscription_registry import (
        broadcast_change_event,
        register_subscription,
    )

    async def fake_evaluate(*, subject, action, resource, _internal_actor=None):
        return _Decision(
            allowed=(subject.id == "user-A"),
            reason="test_filter",
        )

    # Plan 03-02 migrated the per-message filter to policy_engine.evaluate.
    monkeypatch.setattr(
        "app.services.event_subscription_registry.policy_evaluate",
        fake_evaluate,
    )

    ws_a, ws_b = AsyncMock(), AsyncMock()
    await register_subscription("user-A", ws_a)
    await register_subscription("user-B", ws_b)

    await broadcast_change_event(_new_change_event("app:S1"))

    assert ws_a.send_text.await_count == 1
    assert ws_b.send_text.await_count == 0


@pytest.mark.asyncio
async def test_broadcast_user_scope_identity_check():
    """D-07 — user scope: only the matching subscriber receives (identity check)."""
    from app.services.event_subscription_registry import (
        broadcast_change_event,
        register_subscription,
    )

    ws_a, ws_b = AsyncMock(), AsyncMock()
    await register_subscription("user-A", ws_a)
    await register_subscription("user-B", ws_b)

    await broadcast_change_event(_new_change_event("user:user-A"))

    assert ws_a.send_text.await_count == 1
    assert ws_b.send_text.await_count == 0


@pytest.mark.asyncio
async def test_broadcast_unknown_scope_drops_silently():
    """Defensive drop: unknown scope shape → no broadcasts.

    Plan 03-02: the unknown-scope drop now lives in the engine's
    default-human dispatch (``_evaluate_human_default`` returns False when
    ``scope_str`` does not start with ``track:`` / ``space:`` / ``user:``).
    No monkeypatch needed — the real engine call returns ``Decision(allowed=False)``.
    """
    from app.services.event_subscription_registry import (
        broadcast_change_event,
        register_subscription,
    )

    ws = AsyncMock()
    await register_subscription("user-A", ws)

    await broadcast_change_event(_new_change_event("garbage"))

    assert ws.send_text.await_count == 0


@pytest.mark.asyncio
async def test_dead_connection_cleanup(monkeypatch):
    """A ws whose send_text raises is removed from the registry; survivors still receive."""
    from app.schemas.policy import Decision as _Decision
    from app.services.event_subscription_registry import (
        _subscriptions,
        broadcast_change_event,
        register_subscription,
    )

    async def always_allow(*args, **kwargs):
        return _Decision(allowed=True, reason="test_bypass")

    # Plan 03-02 migrated the per-message filter to policy_engine.evaluate.
    monkeypatch.setattr(
        "app.services.event_subscription_registry.policy_evaluate", always_allow
    )

    ws_dead = AsyncMock()
    ws_dead.send_text.side_effect = RuntimeError("connection closed")
    ws_alive = AsyncMock()

    await register_subscription("user-A", ws_dead)
    await register_subscription("user-B", ws_alive)

    await broadcast_change_event(_new_change_event("track:T1"))

    # The dead ws was attempted exactly once, then removed.
    assert ws_dead.send_text.await_count == 1
    assert "user-A" not in _subscriptions
    # The healthy ws still received.
    assert ws_alive.send_text.await_count == 1
    assert "user-B" in _subscriptions


@pytest.mark.asyncio
async def test_revocation_takes_effect_immediately(monkeypatch):
    """D-07 invariant: permission revoked between broadcasts → second is filtered."""
    from app.schemas.policy import Decision as _Decision
    from app.services.event_subscription_registry import (
        broadcast_change_event,
        register_subscription,
    )

    state = {"granted": True}

    async def fake_evaluate(*args, **kwargs):
        return _Decision(
            allowed=state["granted"],
            reason="test_granted" if state["granted"] else "test_revoked",
        )

    # Plan 03-02 migrated the per-message filter to policy_engine.evaluate.
    monkeypatch.setattr(
        "app.services.event_subscription_registry.policy_evaluate",
        fake_evaluate,
    )

    ws = AsyncMock()
    await register_subscription("user-A", ws)

    await broadcast_change_event(_new_change_event("track:T1"))
    assert ws.send_text.await_count == 1

    state["granted"] = False
    await broadcast_change_event(_new_change_event("track:T1"))
    # Second broadcast filtered — count must NOT increment.
    assert ws.send_text.await_count == 1


@pytest.mark.asyncio
async def test_broadcast_payload_shape(monkeypatch):
    """Wire payload mirrors ChangeEventResponse shape (id/ts/actor/action/...)."""
    import json

    from app.schemas.policy import Decision as _Decision
    from app.services.event_subscription_registry import (
        broadcast_change_event,
        register_subscription,
    )

    async def always_allow(*args, **kwargs):
        return _Decision(allowed=True, reason="test_bypass")

    # Plan 03-02 migrated the per-message filter to policy_engine.evaluate.
    monkeypatch.setattr(
        "app.services.event_subscription_registry.policy_evaluate", always_allow
    )

    ws = AsyncMock()
    await register_subscription("user-A", ws)

    event = _new_change_event("track:T1", action="entry.create")
    await broadcast_change_event(event)

    sent_text = ws.send_text.await_args.args[0]
    parsed = json.loads(sent_text)

    assert parsed["type"] == "change_event"
    assert "timestamp" in parsed
    payload = parsed["payload"]
    assert payload["action"] == "entry.create"
    assert payload["resource_type"] == "Entry"
    assert payload["scope"] == "track:T1"
    assert payload["actor"]["kind"] == "human"
    assert payload["actor"]["id"] == "actor-1"
    assert payload["after"] == {"id": "e-1"}
    assert payload["before"] is None


@pytest.mark.asyncio
async def test_broadcast_with_no_subscribers_is_noop(monkeypatch):
    """Empty registry → broadcast returns silently without raising."""
    from app.services.event_subscription_registry import broadcast_change_event

    # No registrations.
    await broadcast_change_event(_new_change_event("track:T1"))
