"""Scripted Realtime acceptance for exact durable voice completions."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import chat_turn_submissions, native_voice
from tests.test_native_voice import Browser, Provider, event, request


async def _send_tool(provider, response_id, call_id, operation="request"):
    await provider.inbox.put(
        {"type": "response.created", "response": {"id": response_id}}
    )
    item = request(call_id, response_id, "Check the current chat")
    item["response"]["output"][0]["arguments"] = json.dumps(
        {"request": "Check the current chat", "operation": operation}
    )
    await provider.inbox.put(item)


@pytest.mark.asyncio
@pytest.mark.parametrize("initial", ["running", "waiting_for_human"])
async def test_completion_follows_ordinary_speech_without_new_work(
    monkeypatch, initial
):
    """Hold a submitted result through an ordinary turn, then announce it once."""
    browser, provider = Browser(), Provider()
    scope = native_voice.VoiceScope("caller", "workspace", "", "thread")
    thread = SimpleNamespace(id=scope.thread)
    receipt = SimpleNamespace(work_item_id="work", message_id="accepted")
    check = AsyncMock(return_value=thread)
    submit = AsyncMock(return_value=receipt)
    current = [initial, None]

    async def status(voice_scope, bound_thread, accepted):
        assert voice_scope is scope and bound_thread is thread
        assert (accepted.work_item_id, accepted.message_id) == ("work", "accepted")
        return tuple(current)

    monkeypatch.setattr(native_voice, "authorize", check)
    monkeypatch.setattr(native_voice, "_delegated_status", status)
    monkeypatch.setattr(chat_turn_submissions, "submit_chat_turn", submit)
    monkeypatch.setattr(native_voice, "RESULT_IDLE_SECONDS", 0.01)
    monkeypatch.setattr(native_voice, "RESULT_POLL_SECONDS", 0.01)
    relay = native_voice.VoiceRelay(browser, provider, scope, check=check)
    call = asyncio.create_task(relay.run())
    try:
        await event(provider.outbox)
        await provider.inbox.put({"type": "session.updated"})
        await event(browser.outbox)
        await _send_tool(provider, "delegate", "first")
        first = (await event(provider.outbox))["item"]
        assert first["call_id"] == "first"
        if initial == "waiting_for_human":
            immediate = await event(provider.outbox)
            assert (
                "has not been completed"
                in immediate.get("response", {}).get("instructions", "")
                or immediate["type"] == "response.create"
            )
        await provider.inbox.put({"type": "input_audio_buffer.speech_started"})
        assert await event(browser.outbox) == {"type": "flush"}

        current[:] = ["succeeded", "The exact workspace result is ready."]
        await provider.inbox.put({"type": "input_audio_buffer.speech_stopped"})
        await provider.inbox.put(
            {"type": "response.created", "response": {"id": "ordinary"}}
        )
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(provider.outbox.get(), 0.05)

        await provider.inbox.put(
            {
                "type": "response.done",
                "response": {"id": "ordinary", "status": "completed"},
            }
        )
        old = await event(provider.outbox)
        assert "exact workspace result" in old["response"]["instructions"]
        assert (old["response"]["tool_choice"], old["response"]["conversation"]) == (
            "none",
            "none",
        )

        # A new user turn beats an announcement that has not emitted audio.
        await provider.inbox.put({"type": "input_audio_buffer.speech_started"})
        assert await event(browser.outbox) == {"type": "flush"}
        await provider.inbox.put(
            {
                "type": "response.created",
                "response": {"id": "stale", "metadata": old["response"]["metadata"]},
            }
        )
        await provider.inbox.put(
            {
                "type": "response.output_audio.delta",
                "response_id": "stale",
                "delta": "AAE=",
            }
        )
        await provider.inbox.put({"type": "input_audio_buffer.speech_stopped"})
        await provider.inbox.put(
            {"type": "response.created", "response": {"id": "ordinary-two"}}
        )
        await provider.inbox.put(
            {
                "type": "response.done",
                "response": {"id": "ordinary-two", "status": "completed"},
            }
        )
        retry = await event(provider.outbox)
        assert retry["response"]["metadata"] != old["response"]["metadata"]
        await provider.inbox.put(
            {
                "type": "response.created",
                "response": {
                    "id": "delivery",
                    "metadata": retry["response"]["metadata"],
                },
            }
        )
        await provider.inbox.put(
            {
                "type": "response.output_audio.delta",
                "response_id": "delivery",
                "delta": "AAI=",
            }
        )
        assert await event(browser.outbox) == b"\x00\x02"
        await provider.inbox.put(request("not-an-ordinary-tool", "delivery", "Echo"))
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(provider.outbox.get(), 0.05)
        submit.assert_awaited_once()
    finally:
        await browser.inbox.put({"type": "websocket.disconnect"})
        await call


@pytest.mark.asyncio
async def test_check_chat_reads_current_receipt_even_while_heavy_pending(monkeypatch):
    """Explicit chat status is read-only and can distinguish review from applying."""
    thread = SimpleNamespace(id="thread")
    receipt = SimpleNamespace(work_item_id="work", message_id="accepted")
    check = AsyncMock(return_value=thread)
    submit = AsyncMock(return_value=receipt)
    current = AsyncMock(return_value=("running", None))
    monkeypatch.setattr(native_voice, "authorize", check)
    monkeypatch.setattr(native_voice, "_delegated_status", current)
    monkeypatch.setattr(chat_turn_submissions, "submit_chat_turn", submit)
    browser, provider = Browser(), Provider()
    relay = native_voice.VoiceRelay(
        browser,
        provider,
        native_voice.VoiceScope("caller", "workspace", "", "thread"),
        check=check,
    )
    call = asyncio.create_task(relay.run())
    try:
        await event(provider.outbox)
        await provider.inbox.put({"type": "session.updated"})
        await event(browser.outbox)
        await _send_tool(provider, "first", "first")
        assert (await event(provider.outbox))["item"]["call_id"] == "first"
        assert not relay.pending.done()
        current.return_value = ("waiting_for_human", None)
        await _send_tool(provider, "check", "check", operation="check_chat")
        assert (await event(provider.outbox))["item"]["call_id"] == "check"
        read = (await event(provider.outbox))["response"]
        assert "waiting for your review" in read["instructions"]
        assert "has not been applied" in read["instructions"]
        submit.assert_awaited_once()
    finally:
        await browser.inbox.put({"type": "websocket.disconnect"})
        await call


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["no_ack", "completed_without_audio", "error"])
async def test_pending_completion_recovers_when_provider_neither_replies_nor_speaks(
    monkeypatch, failure
):
    """Silence cannot hold the delivery gate; an unplayed tagged result can retry."""
    browser, provider = Browser(), Provider()
    scope = native_voice.VoiceScope("caller", "workspace", "", "thread")
    thread = SimpleNamespace(id=scope.thread)
    receipt = SimpleNamespace(work_item_id="work", message_id="accepted")
    check = AsyncMock(return_value=thread)
    status = AsyncMock(return_value=("running", None))
    cancel = AsyncMock()
    from app.agentive.services import work_items

    monkeypatch.setattr(native_voice, "authorize", check)
    monkeypatch.setattr(native_voice, "_delegated_status", status)
    monkeypatch.setattr(
        chat_turn_submissions, "submit_chat_turn", AsyncMock(return_value=receipt)
    )
    monkeypatch.setattr(work_items, "cancel_work_item", cancel)
    monkeypatch.setattr(native_voice, "RESULT_IDLE_SECONDS", 0.01)
    monkeypatch.setattr(native_voice, "RESULT_POLL_SECONDS", 0.01)
    monkeypatch.setattr(native_voice, "USER_REPLY_GRACE_SECONDS", 0.02)
    monkeypatch.setattr(native_voice, "READOUT_PROGRESS_SECONDS", 0.1)

    relay = native_voice.VoiceRelay(browser, provider, scope, check=check)
    call = asyncio.create_task(relay.run())
    try:
        await event(provider.outbox)
        await provider.inbox.put({"type": "session.updated"})
        await event(browser.outbox)
        await _send_tool(provider, "delegate", "first")
        assert "accepted" in (await event(provider.outbox))["item"]["output"]
        await provider.inbox.put({"type": "input_audio_buffer.speech_started"})
        assert await event(browser.outbox) == {"type": "flush"}
        status.return_value = ("succeeded", "A committed result is ready.")
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(provider.outbox.get(), 0.03)
        # The provider detected a brief sound, but did not generate any
        # conversational reply; the caller is now quiet again.
        await provider.inbox.put({"type": "input_audio_buffer.speech_stopped"})
        initial = await event(provider.outbox)
        assert "A committed result is ready." in initial["response"]["instructions"]
        if failure == "error":
            await provider.inbox.put(
                {"type": "error", "error": {"event_id": initial["event_id"]}}
            )
        if failure == "completed_without_audio":
            await provider.inbox.put(
                {
                    "type": "response.created",
                    "response": {
                        "id": "mute",
                        "metadata": initial["response"]["metadata"],
                    },
                }
            )
            await provider.inbox.put(
                {
                    "type": "response.done",
                    "response": {"id": "mute", "status": "completed"},
                }
            )
        retried = await event(provider.outbox)
        assert initial["event_id"] != retried["event_id"]
        assert "A committed result is ready." in retried["response"]["instructions"]
        await provider.inbox.put(
            {
                "type": "response.created",
                "response": {
                    "id": "played",
                    "metadata": retried["response"]["metadata"],
                },
            }
        )
        await provider.inbox.put(
            {
                "type": "response.output_audio.delta",
                "response_id": "played",
                "delta": "AAE=",
            }
        )
        assert await event(browser.outbox) == b"\x00\x01"
        await provider.inbox.put(
            {
                "type": "response.done",
                "response": {"id": "played", "status": "completed"},
            }
        )
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(provider.outbox.get(), 0.15)
        cancel.assert_not_awaited()
        assert relay.protocol_counts["readout_sent"] == 1
    finally:
        await browser.inbox.put({"type": "websocket.disconnect"})
        await call
