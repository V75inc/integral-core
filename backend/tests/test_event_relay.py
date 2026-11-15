"""Slow clients and permission changes are isolated from event producers."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.services import event_subscription_registry as registry
from app.services.change_event_logger import ChangeEventEnvelope

pytestmark = pytest.mark.asyncio


def event():
    """Build a synthetic event without any private record data."""
    return ChangeEventEnvelope(
        id="synthetic",
        ts="2026-11-10T00:00:00+00:00",
        actor_kind="system",
        actor_id="system",
        action="entry.update",
        resource_type="Entry",
        resource_id="n.Entry.synthetic",
        scope="track:n.Track.synthetic",
    )


async def test_slow_socket_does_not_block_other_clients(monkeypatch):
    """An independent sender services healthy clients while another is stalled."""
    stalled, fast = asyncio.Event(), asyncio.Event()

    async def slow_send(message):
        await stalled.wait()

    async def fast_send(message):
        fast.set()

    slow = type("Socket", (), {})()
    slow.send_text, slow.close = slow_send, AsyncMock()
    healthy = type("Socket", (), {})()
    healthy.send_text, healthy.close = fast_send, AsyncMock()
    monkeypatch.setattr(
        registry, "prepare_event_payload", AsyncMock(return_value={"id": "synthetic"})
    )
    await registry.register_subscription("slow", slow)
    await registry.register_subscription("fast", healthy)
    try:
        registry.queue_change_event(event())
        await asyncio.wait_for(fast.wait(), 1)
        assert not stalled.is_set()
    finally:
        await registry.unregister_subscription("slow", slow)
        await registry.unregister_subscription("fast", healthy)


async def test_permission_is_checked_when_queued_event_is_sent(monkeypatch):
    """Revocation after enqueue withholds the event before the actual send."""
    checked = asyncio.Event()
    allowed = True

    async def authorize(*args: object, **kwargs: object):
        checked.set()
        return {"id": "synthetic"} if allowed else None

    socket = type("Socket", (), {})()
    socket.send_text, socket.close = AsyncMock(), AsyncMock()
    monkeypatch.setattr(registry, "prepare_event_payload", authorize)
    await registry.register_subscription("revoked", socket)
    try:
        registry.queue_change_event(event())
        allowed = False
        await asyncio.wait_for(checked.wait(), 1)
        await asyncio.sleep(0)
        socket.send_text.assert_not_awaited()
    finally:
        await registry.unregister_subscription("revoked", socket)


async def test_overflow_disconnects_even_before_sender_starts(monkeypatch):
    """Full queues cannot retain a registered client whose sender was cancelled early."""
    socket = type("Socket", (), {})()
    socket.send_text, socket.close = AsyncMock(), AsyncMock()
    await registry.register_subscription("overflow", socket)
    try:
        for _ in range(101):
            registry.queue_change_event(event())
        for _ in range(10):
            await asyncio.sleep(0)
        assert "overflow" not in registry._subscriptions
        socket.close.assert_awaited()
    finally:
        await registry.unregister_subscription("overflow", socket)
