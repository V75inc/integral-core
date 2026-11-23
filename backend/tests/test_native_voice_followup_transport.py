"""Voice tool follow-ups are explicit, scope-fenced and do not block audio."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.errors import ResourceConflictError
from app.services import native_voice, native_voice_followups
from tests.test_native_voice import Browser, Provider, authorize, event
from tests.test_native_voice_completion import _send_tool


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "active_id", "result_status", "expected"),
    [
        ("queue", "active", "queued", "queued that as a separate"),
        (
            "steer",
            "active",
            "steering_accepted",
            "not confirmed the change was applied",
        ),
        ("steer", "new", None, "same voice request"),
        ("queue", None, None, "no active request"),
        ("queue", "active", None, "couldn't attach"),
    ],
)
async def test_follow_up_disposition_is_explicit_and_never_falls_back(
    monkeypatch, operation, active_id, result_status, expected
):
    """Check the sent status and prove no implicit rerouting or duplicate work."""
    scope = native_voice.VoiceScope("caller", "workspace", "", "thread")
    target = SimpleNamespace(fence=SimpleNamespace(work_item_id=active_id))
    resolve = AsyncMock(return_value=target if active_id else None)
    route = AsyncMock(
        return_value=SimpleNamespace(
            status=result_status, client_request_id="test-queue"
        )
    )
    if result_status is None and expected == "couldn't attach":
        route.side_effect = ResourceConflictError(message="private detail")
    monkeypatch.setattr(
        native_voice_followups, "resolve_voice_follow_up_target", resolve
    )
    monkeypatch.setattr(native_voice_followups, "route_voice_follow_up", route)
    browser, provider = Browser(), Provider()
    delegate = AsyncMock()
    relay = native_voice.VoiceRelay(
        browser, provider, scope, check=authorize, work=delegate
    )
    relay.accepted = native_voice._AcceptedVoiceTurn("active", "message", "original")
    task = asyncio.create_task(relay.run())
    try:
        await event(provider.outbox)
        await provider.inbox.put({"type": "session.updated"})
        await event(browser.outbox)
        await _send_tool(provider, "followup", "followup", operation)
        output = await event(provider.outbox)
        assert expected in output["item"]["output"]
        assert "private detail" not in output["item"]["output"]
        assert (await event(provider.outbox))["response"]["tool_choice"] == "none"
        await _send_tool(provider, "replay", "followup", operation)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(provider.outbox.get(), 0.05)
        resolve.assert_awaited_once_with(scope)
        if result_status is not None or expected == "couldn't attach":
            route.assert_awaited_once_with(
                scope,
                "followup",
                "Check the current chat",
                target=target,
                operation=operation,
                check=authorize,
            )
        else:
            route.assert_not_awaited()
        delegate.assert_not_awaited()
    finally:
        await browser.inbox.put({"type": "websocket.disconnect"})
        await task


@pytest.mark.asyncio
async def test_spoken_follow_up_does_not_block_ordinary_conversation(monkeypatch):
    """Continue voice while a queue transaction is in progress."""
    entered, release = asyncio.Event(), asyncio.Event()
    target = SimpleNamespace(fence=SimpleNamespace(work_item_id="active"))
    monkeypatch.setattr(
        native_voice_followups,
        "resolve_voice_follow_up_target",
        AsyncMock(return_value=target),
    )

    async def route(_scope, _call_id, _text, *, target, operation, check):
        entered.set()
        await release.wait()
        return SimpleNamespace(status="queued", client_request_id="test-queue")

    monkeypatch.setattr(native_voice_followups, "route_voice_follow_up", route)
    scope = native_voice.VoiceScope("caller", "workspace", "", "thread")
    browser, provider = Browser(), Provider()
    relay = native_voice.VoiceRelay(browser, provider, scope, check=authorize)
    task = asyncio.create_task(relay.run())
    try:
        await event(provider.outbox)
        await provider.inbox.put({"type": "session.updated"})
        await event(browser.outbox)
        await _send_tool(provider, "followup", "followup", "queue")
        await asyncio.wait_for(entered.wait(), 2)
        await provider.inbox.put({"type": "input_audio_buffer.speech_started"})
        assert await event(browser.outbox) == {"type": "flush"}
        await provider.inbox.put(
            {"type": "response.created", "response": {"id": "ordinary"}}
        )
        await provider.inbox.put(
            {
                "type": "response.output_audio.delta",
                "response_id": "ordinary",
                "delta": "AAE=",
            }
        )
        assert await event(browser.outbox) == b"\x00\x01"
        release.set()
        output = await event(provider.outbox)
        assert "queued that as a separate" in output["item"]["output"]
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(provider.outbox.get(), 0.05)
    finally:
        release.set()
        await browser.inbox.put({"type": "websocket.disconnect"})
        await task
