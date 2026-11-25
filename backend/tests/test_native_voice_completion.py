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


async def completed_readout(provider, expected):
    context = await event(provider.outbox)
    assert context["type"] == "conversation.item.create"
    assert context["item"]["role"] == "system"
    content = context["item"]["content"][0]["text"]
    assert "historical data" in content and "permission" in content
    assert expected in content
    return await event(provider.outbox)


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
        old = await completed_readout(provider, "exact workspace result")
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
        initial = await completed_readout(provider, "A committed result")
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


@pytest.mark.asyncio
async def test_visible_admission_remains_watched_if_initial_status_is_unavailable(
    monkeypatch,
):
    """An admitted turn may be visible in chat before voice can read its outcome."""
    browser, provider = Browser(), Provider()
    scope = native_voice.VoiceScope("caller", "workspace", "", "thread")
    check = AsyncMock(return_value=SimpleNamespace(id=scope.thread))
    submit = AsyncMock(
        return_value=SimpleNamespace(work_item_id="admitted", message_id="user-message")
    )
    observed = AsyncMock(
        side_effect=[
            TimeoutError(),
            ("unavailable", None),
            ("succeeded", "Verified result from chat."),
        ]
    )
    monkeypatch.setattr(native_voice, "authorize", check)
    monkeypatch.setattr(native_voice, "_delegated_status", observed)
    monkeypatch.setattr(chat_turn_submissions, "submit_chat_turn", submit)
    monkeypatch.setattr(native_voice, "RESULT_POLL_SECONDS", 0.02)
    monkeypatch.setattr(native_voice, "RESULT_IDLE_SECONDS", 0.01)
    monkeypatch.setattr(native_voice, "RESULT_UNVERIFIED_BACKOFF_SECONDS", 0.02)

    relay = native_voice.VoiceRelay(browser, provider, scope, check=check)
    call = asyncio.create_task(relay.run())
    try:
        await event(provider.outbox)
        await provider.inbox.put({"type": "session.updated"})
        await event(browser.outbox)
        await _send_tool(provider, "initial", "initial")
        output = await event(provider.outbox)
        assert "accepted" in output["item"]["output"]
        assert relay.accepted.work_item_id == "admitted"
        acknowledgment = await event(provider.outbox)
        await provider.inbox.put(
            {
                "type": "response.created",
                "response": {
                    "id": "ack",
                    "metadata": acknowledgment["response"]["metadata"],
                },
            }
        )
        await provider.inbox.put(
            {"type": "response.done", "response": {"id": "ack", "status": "completed"}}
        )
        result = await completed_readout(provider, "Verified result from chat")
        assert "Verified result from chat." in result["response"]["instructions"]
        assert relay.protocol_counts["durable_result_unverified"] == 1
        await provider.inbox.put(
            {
                "type": "response.created",
                "response": {
                    "id": "terminal",
                    "metadata": result["response"]["metadata"],
                },
            }
        )
        await provider.inbox.put(
            {
                "type": "response.output_audio.delta",
                "response_id": "terminal",
                "delta": "AAE=",
            }
        )
        assert await event(browser.outbox) == b"\x00\x01"
        assert relay.protocol_counts["durable_admitted"] == 1
        assert relay.protocol_counts["durable_terminal_verified"] == 1
        assert relay.protocol_counts["readout_sent"] == 1
        assert relay.protocol_counts["delegate_operation_request"] == 1
        submit.assert_awaited_once()
    finally:
        await browser.inbox.put({"type": "websocket.disconnect"})
        await call


@pytest.mark.asyncio
async def test_running_work_announces_pending_review_once_then_actual_result(
    monkeypatch,
):
    """One intermediate readout leaves the receipt available for the terminal result."""
    browser, provider = Browser(), Provider()
    thread = SimpleNamespace(id="thread")
    scope = native_voice.VoiceScope("caller", "workspace", "", thread.id)
    check = AsyncMock(return_value=thread)
    current = ["running", None]
    receipt = SimpleNamespace(work_item_id="work", message_id="accepted")
    monkeypatch.setattr(native_voice, "authorize", check)
    monkeypatch.setattr(
        native_voice,
        "_delegated_status",
        AsyncMock(side_effect=lambda *_: tuple(current)),
    )
    monkeypatch.setattr(
        chat_turn_submissions, "submit_chat_turn", AsyncMock(return_value=receipt)
    )
    monkeypatch.setattr(native_voice, "RESULT_IDLE_SECONDS", 0.01)
    monkeypatch.setattr(native_voice, "RESULT_POLL_SECONDS", 0.01)
    relay = native_voice.VoiceRelay(browser, provider, scope, check=check)
    call = asyncio.create_task(relay.run())
    try:
        await event(provider.outbox)
        await provider.inbox.put({"type": "session.updated"})
        await event(browser.outbox)
        await _send_tool(provider, "r1", "c1")
        assert "accepted" in (await event(provider.outbox))["item"]["output"]
        current[:] = ["waiting_for_human", None]
        review = (await event(provider.outbox))["response"]
        assert "not been applied" in review["instructions"]
        await provider.inbox.put(
            {
                "type": "response.created",
                "response": {"id": "review", "metadata": review["metadata"]},
            }
        )
        await provider.inbox.put(
            {
                "type": "response.output_audio.delta",
                "response_id": "review",
                "delta": "AAI=",
            }
        )
        assert await event(browser.outbox) == b"\x00\x02"
        await provider.inbox.put(
            {
                "type": "response.done",
                "response": {"id": "review", "status": "completed"},
            }
        )
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(provider.outbox.get(), 0.06)
        assert "work" in relay.result_receipts
        current[:] = ["succeeded", "The record is saved."]
        final = (await completed_readout(provider, "The record is saved."))["response"]
        assert "The record is saved." in final["instructions"]
    finally:
        await browser.inbox.put({"type": "websocket.disconnect"})
        await call


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["running", "succeeded", "waiting_for_human"])
async def test_follow_through_can_advance_visible_completed_work(monkeypatch, status):
    """A new explicit action progresses after a visible plan, with review intact."""
    scope = native_voice.VoiceScope("caller", "workspace", "", "thread")
    thread = SimpleNamespace(id=scope.thread)
    browser, provider = Browser(), Provider()
    relay = native_voice.VoiceRelay(
        browser, provider, scope, check=AsyncMock(return_value=thread)
    )
    relay.accepted = native_voice._AcceptedVoiceTurn(
        "plan", "message", "Plan a workspace app"
    )
    relay.pending = asyncio.create_task(asyncio.Event().wait())
    previous = relay.pending
    queue = AsyncMock()
    monkeypatch.setattr(relay, "_follow_up", queue)
    state = (status, "The plan is ready." if status == "succeeded" else None)
    monkeypatch.setattr(
        native_voice, "_delegated_status", AsyncMock(return_value=state)
    )
    perform = AsyncMock()
    monkeypatch.setattr(relay, "_work", perform)
    try:
        await relay._start_requested_work(
            "build", "Implement the plan in our chat", "request"
        )
        if status == "succeeded":
            await relay.pending
            assert previous.cancelled()
            perform.assert_awaited_once_with(
                "build",
                "Implement the plan in our chat",
                relay.generation,
                None,
                open_only=False,
            )
        else:
            assert relay.pending is previous
            perform.assert_not_awaited()
            if status == "waiting_for_human":
                assert (
                    "still active" in (await event(provider.outbox))["item"]["output"]
                )
                queue.assert_not_awaited()
                return
            await relay.follow_up_task
            queue.assert_awaited_once_with(
                "build",
                "Implement the plan in our chat",
                "queue",
                relay.generation,
                relay.accepted,
            )
    finally:
        previous.cancel()
        await asyncio.gather(previous, return_exceptions=True)
