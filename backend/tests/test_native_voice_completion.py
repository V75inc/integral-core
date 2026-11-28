"""Scripted Realtime acceptance for exact durable voice completions."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import chat_turn_submissions, native_voice
from tests.test_native_voice import Browser, Provider, event, request


async def _send_tool(
    provider, response_id, call_id, operation="request", delivery="voice"
):
    await provider.inbox.put(
        {"type": "response.created", "response": {"id": response_id}}
    )
    item = request(call_id, response_id, "Check the current chat")
    item["response"]["output"][0]["arguments"] = json.dumps(
        {
            "request": "Check the current chat",
            "operation": operation,
            "delivery": delivery,
        }
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
@pytest.mark.parametrize(
    ("initial", "delivery"),
    [("running", "voice"), ("running", "written_only"), ("waiting_for_human", "voice")],
)
async def test_completion_follows_ordinary_speech_without_new_work(
    monkeypatch, initial, delivery
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
        await _send_tool(provider, "delegate", "first", delivery=delivery)
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

        if initial == "running":
            tools_update = await event(provider.outbox)
            assert tools_update["type"] == "session.update"
            assert native_voice.STEER_ACTIVE in {
                tool["name"] for tool in tools_update["session"]["tools"]
            }
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
        expected = (
            native_voice._WRITTEN_RECEIPT
            if delivery == "written_only"
            else "exact workspace result"
        )
        assert expected in old["response"]["instructions"]
        if delivery == "written_only":
            assert "exact workspace result" not in old["response"]["instructions"]
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
        if initial == "running":
            update = await event(provider.outbox)
            assert update["type"] == "session.update"
            assert native_voice.STEER_ACTIVE not in {
                t["name"] for t in update["session"]["tools"]
            }
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
        await provider.inbox.put({"type": "input_audio_buffer.speech_started"})
        assert await event(browser.outbox) == {"type": "flush"}
        assert (await event(provider.outbox))["type"] == "session.update"
        await provider.inbox.put({"type": "input_audio_buffer.speech_stopped"})
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
        tools_update = await event(provider.outbox)
        assert tools_update["type"] == "session.update"
        assert native_voice.STEER_ACTIVE in {
            tool["name"] for tool in tools_update["session"]["tools"]
        }
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "delivered", "remember"),
    [
        ("completed", True, True),
        ("cancelled", True, False),
        ("completed", False, False),
    ],
)
async def test_completed_spoken_review_retains_only_question(
    status, delivered, remember
):
    provider = Provider()
    relay = native_voice.VoiceRelay(
        Browser(),
        provider,
        native_voice.VoiceScope("caller", "workspace", "", "thread"),
    )
    relay.navigation_readout = native_voice.VoiceReviewReadout(
        "Private record text",
        "work-id",
        "run-id",
        "proposal-token",
        "target-id",
        "fingerprint",
    )
    relay.speech_only_response_id = relay.response_generation = "readout"
    relay.readout_audio_verified = delivered
    reader = asyncio.create_task(relay._read_provider())
    try:
        await provider.inbox.put(
            {"type": "response.done", "response": {"id": "readout", "status": status}}
        )
        if remember:
            item = (await event(provider.outbox))["item"]
            assert item["role"] == "assistant"
            assert "approve or reject" in item["content"][0]["text"]
            assert all(
                value not in str(item)
                for value in (
                    "Private record text",
                    "proposal-token",
                    "target-id",
                    "work-id",
                    "fingerprint",
                )
            )
        else:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(provider.outbox.get(), 0.1)
    finally:
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)


@pytest.mark.asyncio
async def test_slow_task_progress_is_quiet_verified_bounded_and_final_once(monkeypatch):
    browser, provider = Browser(), Provider()
    scope = native_voice.VoiceScope("caller", "workspace", "", "thread")
    current = ["running", None]
    check = AsyncMock(return_value=SimpleNamespace(id=scope.thread))
    submit = AsyncMock(
        return_value=SimpleNamespace(work_item_id="slow", message_id="accepted")
    )
    monkeypatch.setattr(native_voice, "authorize", check)
    monkeypatch.setattr(chat_turn_submissions, "submit_chat_turn", submit)
    monkeypatch.setattr(
        native_voice,
        "_delegated_status",
        AsyncMock(side_effect=lambda *a: tuple(current)),
    )
    monkeypatch.setattr(native_voice, "RESULT_IDLE_SECONDS", 0.001)
    monkeypatch.setattr(native_voice, "RESULT_POLL_SECONDS", 0.005)
    monkeypatch.setattr(native_voice, "WORK_PROGRESS_INTERVALS", (0.04, 0.08, 0.16))
    relay = native_voice.VoiceRelay(browser, provider, scope, check=check)
    call = asyncio.create_task(relay.run())

    async def speech(frame, response_id):
        await provider.inbox.put(
            {
                "type": "response.created",
                "response": {
                    "id": response_id,
                    "metadata": frame["response"]["metadata"],
                },
            }
        )
        await provider.inbox.put(
            {
                "type": "response.output_audio.delta",
                "response_id": response_id,
                "delta": "AAE=",
            }
        )
        assert await event(browser.outbox) == b"\x00\x01"
        await provider.inbox.put(
            {
                "type": "response.done",
                "response": {"id": response_id, "status": "completed"},
            }
        )

    try:
        await event(provider.outbox)
        await provider.inbox.put({"type": "session.updated"})
        await event(browser.outbox)
        await _send_tool(provider, "initial", "initial")
        assert "accepted" in (await event(provider.outbox))["item"]["output"]
        await provider.inbox.put({"type": "input_audio_buffer.speech_started"})
        assert await event(browser.outbox) == {"type": "flush"}
        assert (await event(provider.outbox))["type"] == "session.update"
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(provider.outbox.get(), 0.08)
        await provider.inbox.put({"type": "input_audio_buffer.speech_stopped"})
        await provider.inbox.put(
            {"type": "response.created", "response": {"id": "aside"}}
        )
        await provider.inbox.put(
            {
                "type": "response.done",
                "response": {"id": "aside", "status": "completed"},
            }
        )
        first = await event(provider.outbox)
        assert native_voice.WORK_PROGRESS_LINES[0] in first["response"]["instructions"]
        await speech(first, "progress1")
        second = await event(provider.outbox)
        assert native_voice.WORK_PROGRESS_LINES[1] in second["response"]["instructions"]
        await speech(second, "progress2")
        current[:] = ["succeeded", "The requested report is ready."]
        result = await completed_readout(provider, "The requested report is ready.")
        await speech(result, "result")
        await asyncio.sleep(0.02)
        assert relay.protocol_counts["readout_sent"] == 1
        assert not call.done()
        submit.assert_awaited_once()
        # A fresh voice turn is not blocked after the completion.
        await provider.inbox.put({"type": "input_audio_buffer.speech_started"})
        assert await event(browser.outbox) == {"type": "flush"}
        await provider.inbox.put({"type": "input_audio_buffer.speech_stopped"})
        await provider.inbox.put(
            {"type": "response.created", "response": {"id": "next"}}
        )
        await provider.inbox.put(
            {
                "type": "response.output_audio.delta",
                "response_id": "next",
                "delta": "AAE=",
            }
        )
        assert await event(browser.outbox) == b"\x00\x01"
        assert not call.done()
    finally:
        await browser.inbox.put({"type": "websocket.disconnect"})
        await call


@pytest.mark.asyncio
async def test_call_duration_limit_has_distinct_explanation(monkeypatch):
    browser, provider = Browser(), Provider()
    relay = native_voice.VoiceRelay(
        browser,
        provider,
        native_voice.VoiceScope("caller", "workspace", "", "thread"),
        check=AsyncMock(),
    )
    monkeypatch.setattr(native_voice, "MAX_CALL_SECONDS", 0.03)
    call = asyncio.create_task(relay.run())
    await event(provider.outbox)
    explanation = await event(browser.outbox)
    assert explanation["type"] == "error"
    assert "connection time limit" in explanation["message"]
    await call


@pytest.mark.asyncio
@pytest.mark.parametrize("synthesis_done", [False, True])
async def test_completed_results_wait_for_smalltalk_and_preserve_each_other(
    monkeypatch, synthesis_done
):
    """Test the actual relay loop with overlapping audio, two receipts, and a following turn."""
    browser, provider = Browser(), Provider()
    scope = native_voice.VoiceScope("caller", "workspace", "", "thread")
    thread = SimpleNamespace(id=scope.thread)
    check = AsyncMock(return_value=thread)
    submit = AsyncMock(
        return_value=SimpleNamespace(work_item_id="first", message_id="accepted")
    )
    outcomes = {"first": ("running", None), "second": ("running", None)}
    monkeypatch.setattr(native_voice, "authorize", check)
    monkeypatch.setattr(chat_turn_submissions, "submit_chat_turn", submit)
    monkeypatch.setattr(
        native_voice,
        "_delegated_status",
        AsyncMock(
            side_effect=lambda _scope, _thread, receipt: outcomes[receipt.work_item_id]
        ),
    )
    monkeypatch.setattr(native_voice, "RESULT_IDLE_SECONDS", 0.01)
    monkeypatch.setattr(native_voice, "RESULT_POLL_SECONDS", 0.01)
    relay = native_voice.VoiceRelay(browser, provider, scope, check=check)
    call = asyncio.create_task(relay.run())
    try:
        await event(provider.outbox)
        await provider.inbox.put({"type": "session.updated"})
        await event(browser.outbox)
        await _send_tool(provider, "initial", "first")
        assert "accepted" in (await event(provider.outbox))["item"]["output"]
        # Another independently admitted receipt (as from the FIFO promotion path).
        relay.result_receipts["second"] = native_voice._AcceptedVoiceTurn(
            "second", "accepted-two", "Second task", queued_follow_up=True
        )
        await provider.inbox.put({"type": "input_audio_buffer.speech_started"})
        assert await event(browser.outbox) == {"type": "flush"}
        assert (await event(provider.outbox))["type"] == "session.update"
        await provider.inbox.put({"type": "input_audio_buffer.speech_stopped"})
        await provider.inbox.put(
            {"type": "response.created", "response": {"id": "smalltalk"}}
        )
        stale_audio = {
            "type": "response.output_audio.delta",
            "response_id": "smalltalk",
            "item_id": "smalltalk-item",
            "content_index": 0,
            "delta": "AAE=",
        }
        await provider.inbox.put(stale_audio)
        marker = await event(browser.outbox)
        assert marker["type"] == "playback_item"
        assert await event(browser.outbox) == b"\x00\x01"
        if synthesis_done:
            await provider.inbox.put(
                {
                    "type": "response.done",
                    "response": {"id": "smalltalk", "status": "completed"},
                }
            )
        # A running job and ordinary silence/speech are not a priority signal.
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(browser.outbox.get(), 0.03)
        outcomes["first"] = ("succeeded", "The first report is ready.")
        relay.result_wake.set()
        if not synthesis_done:
            # A queued resident result must not interrupt a sentence or
            # discard already-buffered ordinary speech.
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(browser.outbox.get(), 0.03)
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(provider.outbox.get(), 0.03)
            await provider.inbox.put(stale_audio)
            assert await event(browser.outbox) == b"\x00\x01"
            await provider.inbox.put(
                {
                    "type": "response.done",
                    "response": {"id": "smalltalk", "status": "completed"},
                }
            )
        result = await completed_readout(provider, "The first report is ready.")
        assert "The first report is ready." in result["response"]["instructions"]
        assert browser.outbox.empty()
        await provider.inbox.put(stale_audio)
        await provider.inbox.put(
            {
                "type": "response.created",
                "response": {
                    "id": "first-result",
                    "metadata": result["response"]["metadata"],
                },
            }
        )
        await provider.inbox.put(
            {
                "type": "response.output_audio.delta",
                "response_id": "first-result",
                "delta": "AgM=",
            }
        )
        assert await event(browser.outbox) == {"type": "playback_item", "handle": None}
        assert await event(browser.outbox) == b"\x02\x03"
        outcomes["second"] = ("succeeded", "The second report is ready.")
        relay.result_wake.set()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(provider.outbox.get(), 0.03)
        await provider.inbox.put(
            {
                "type": "response.output_audio.delta",
                "response_id": "first-result",
                "delta": "BAU=",
            }
        )
        assert await event(browser.outbox) == b"\x04\x05"
        await provider.inbox.put(
            {
                "type": "response.done",
                "response": {"id": "first-result", "status": "completed"},
            }
        )
        second = await completed_readout(provider, "The second report is ready.")
        assert "The second report is ready." in second["response"]["instructions"]
        await provider.inbox.put(
            {
                "type": "response.created",
                "response": {
                    "id": "second-result",
                    "metadata": second["response"]["metadata"],
                },
            }
        )
        await provider.inbox.put(
            {
                "type": "response.output_audio.delta",
                "response_id": "second-result",
                "delta": "Bgc=",
            }
        )
        assert await event(browser.outbox) == b"\x06\x07"
        await provider.inbox.put(
            {
                "type": "response.done",
                "response": {"id": "second-result", "status": "completed"},
            }
        )
        await asyncio.sleep(0.03)
        assert relay.protocol_counts["readout_sent"] == 2
        assert relay.protocol_counts["priority_result_interrupt"] == 0
        assert not relay.result_receipts
        assert not call.done()
        submit.assert_awaited_once()
        # A caller can immediately continue with an ordinary fresh turn.
        await provider.inbox.put({"type": "input_audio_buffer.speech_started"})
        assert await event(browser.outbox) == {"type": "flush"}
        await provider.inbox.put({"type": "input_audio_buffer.speech_stopped"})
        await provider.inbox.put(
            {"type": "response.created", "response": {"id": "next"}}
        )
        await provider.inbox.put(
            {
                "type": "response.output_audio.delta",
                "response_id": "next",
                "delta": "CAk=",
            }
        )
        assert await event(browser.outbox) == b"\x08\x09"
        assert not call.done()
    finally:
        await browser.inbox.put({"type": "websocket.disconnect"})
        await call


@pytest.mark.asyncio
async def test_priority_delivery_excludes_user_speech_review_and_tool_response():
    """Only already-audible ordinary speech is eligible for cancellation."""
    browser, provider = Browser(), Provider()
    relay = native_voice.VoiceRelay(
        browser,
        provider,
        native_voice.VoiceScope("caller", "workspace", "", "thread"),
        check=AsyncMock(),
    )
    relay.ready = True
    relay.response_generation = "ordinary"
    relay.last_spoken_response = "ordinary"
    for attr, value in (
        ("speaking", True),
        ("waiting_for_response", True),
        ("review_confirmation", object()),
        ("ordinary_tool_response", "ordinary"),
        ("speech_only_response_id", "ordinary"),
    ):
        previous = getattr(relay, attr)
        setattr(relay, attr, value)
        assert await relay._prepare_priority_delivery(relay.generation) == (
            False,
            False,
        )
        setattr(relay, attr, previous)
    assert browser.outbox.empty()
    assert provider.outbox.empty()
    assert relay.response_generation == "ordinary"


@pytest.mark.asyncio
async def test_completed_tool_during_ordinary_response_processed_before_result(
    monkeypatch,
):
    """A late complete tool must be processed before a queued result, without audio preemption."""
    browser, provider = Browser(), Provider()
    relay = native_voice.VoiceRelay(
        browser,
        provider,
        native_voice.VoiceScope("caller", "workspace", "", "thread"),
        check=AsyncMock(),
    )
    relay.ready = True
    relay.workspace_label = "QA workspace"
    monkeypatch.setattr(native_voice, "RESULT_IDLE_SECONDS", 0.01)
    monkeypatch.setattr(native_voice, "RESULT_POLL_SECONDS", 0.01)
    reader = asyncio.create_task(relay._read_provider())
    watcher = asyncio.create_task(relay._watch_results())
    try:
        await provider.inbox.put(
            {"type": "response.created", "response": {"id": "ordinary"}}
        )
        await provider.inbox.put(
            {
                "type": "response.output_audio.delta",
                "response_id": "ordinary",
                "item_id": "ordinary-item",
                "content_index": 0,
                "delta": "AAE=",
            }
        )
        marker = await event(browser.outbox)
        assert await event(browser.outbox) == b"\x00\x01"
        relay.peer_readouts[("saved-answer", "message")] = "Your report is ready."
        relay.result_wake.set()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(browser.outbox.get(), 0.03)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(provider.outbox.get(), 0.03)
        await provider.inbox.put(
            request(
                "identity-call",
                "ordinary",
                "",
                name=native_voice.CALL_CONTEXT,
                args={"detail": "workspace"},
            )
        )
        assert (await event(provider.outbox))["item"]["call_id"] == "identity-call"
        identity = await event(provider.outbox)
        assert "QA workspace" in identity["response"]["instructions"]
        await provider.inbox.put(
            {
                "type": "response.created",
                "response": {
                    "id": "identity",
                    "metadata": identity["response"]["metadata"],
                },
            }
        )
        await provider.inbox.put(
            {
                "type": "response.output_audio.delta",
                "response_id": "identity",
                "delta": "AgM=",
            }
        )
        assert await event(browser.outbox) == {"type": "playback_item", "handle": None}
        assert await event(browser.outbox) == b"\x02\x03"
        await provider.inbox.put(
            {
                "type": "response.done",
                "response": {"id": "identity", "status": "completed"},
            }
        )
        report = await event(provider.outbox)
        assert "Your report is ready." in report["response"]["instructions"]
        await provider.inbox.put(
            {
                "type": "response.created",
                "response": {
                    "id": "report",
                    "metadata": report["response"]["metadata"],
                },
            }
        )
        await provider.inbox.put(
            {
                "type": "response.output_audio.delta",
                "response_id": "report",
                "delta": "BAU=",
            }
        )
        assert await event(browser.outbox) == b"\x04\x05"
        assert relay.protocol_counts["peer_readout_sent"] == 1
        assert not reader.done()
    finally:
        reader.cancel()
        watcher.cancel()
        await asyncio.gather(reader, watcher, return_exceptions=True)


@pytest.mark.asyncio
async def test_user_barge_in_while_result_waits_preserves_unsent_result(monkeypatch):
    """A caller speaking while an ordinary response is finishing wins and gets the result later."""
    browser, provider = Browser(), Provider()
    relay = native_voice.VoiceRelay(
        browser,
        provider,
        native_voice.VoiceScope("caller", "workspace", "", "thread"),
        check=AsyncMock(),
    )
    relay.ready = True
    monkeypatch.setattr(native_voice, "RESULT_IDLE_SECONDS", 0.01)
    monkeypatch.setattr(native_voice, "RESULT_POLL_SECONDS", 0.01)
    reader = asyncio.create_task(relay._read_provider())
    watcher = asyncio.create_task(relay._watch_results())
    try:
        await provider.inbox.put(
            {"type": "response.created", "response": {"id": "ordinary"}}
        )
        await provider.inbox.put(
            {
                "type": "response.output_audio.delta",
                "response_id": "ordinary",
                "item_id": "ordinary-item",
                "content_index": 0,
                "delta": "AAE=",
            }
        )
        await event(browser.outbox)
        assert await event(browser.outbox) == b"\x00\x01"
        key = ("answer", "message")
        relay.peer_readouts[key] = "Your lookup is ready."
        relay.result_wake.set()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(browser.outbox.get(), 0.03)
        await provider.inbox.put({"type": "input_audio_buffer.speech_started"})
        assert await event(browser.outbox) == {"type": "flush"}
        await provider.inbox.put(
            {
                "type": "response.output_audio.delta",
                "response_id": "ordinary",
                "delta": "AAE=",
            }
        )
        await provider.inbox.put(
            {
                "type": "response.done",
                "response": {"id": "ordinary", "status": "cancelled"},
            }
        )
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(browser.outbox.get(), 0.03)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(provider.outbox.get(), 0.03)
        assert key in relay.peer_readouts
        await provider.inbox.put({"type": "input_audio_buffer.speech_stopped"})
        await provider.inbox.put(
            {"type": "response.created", "response": {"id": "user-request"}}
        )
        await provider.inbox.put(
            {
                "type": "response.done",
                "response": {"id": "user-request", "status": "completed"},
            }
        )
        retry = await event(provider.outbox)
        assert "Your lookup is ready." in retry["response"]["instructions"]
        await provider.inbox.put(
            {
                "type": "response.created",
                "response": {"id": "retry", "metadata": retry["response"]["metadata"]},
            }
        )
        await provider.inbox.put(
            {
                "type": "response.output_audio.delta",
                "response_id": "retry",
                "delta": "AgM=",
            }
        )
        assert await event(browser.outbox) == b"\x02\x03"
        assert not relay.peer_readouts
        assert relay.protocol_counts["peer_readout_sent"] == 1
        assert not reader.done()
    finally:
        reader.cancel()
        watcher.cancel()
        await asyncio.gather(reader, watcher, return_exceptions=True)


@pytest.mark.asyncio
async def test_build_plan_recaps_after_reconciling_still_running_observer(monkeypatch):
    """A saved plan can be terminal while its original relay observer is alive."""
    browser, provider = Browser(), Provider(manual_intent=True)
    scope = native_voice.VoiceScope("caller", "workspace", "", "thread")
    work = AsyncMock(return_value="The app is ready.")
    relay = native_voice.VoiceRelay(
        browser, provider, scope, check=AsyncMock(), work=work
    )
    relay.ready = True
    relay.accepted = native_voice._AcceptedVoiceTurn("plan", "message", "Plan an app")
    relay.pending = asyncio.create_task(asyncio.Event().wait())
    old_observer = relay.pending
    monkeypatch.setattr(
        native_voice,
        "_delegated_status",
        AsyncMock(return_value=("succeeded", "The plan is ready.")),
    )
    reader = asyncio.create_task(relay._read_provider())
    try:
        relay.response_generation = "follow-up"
        await relay._completed_response(
            request(
                "build-once",
                "follow-up",
                "",
                args={
                    "request": "Go ahead and build the app plan.",
                    "intent_summary": "I’ll build the app from the plan.",
                },
            )
        )
        assert old_observer.cancelled()
        summary = (await event(provider.outbox))["response"]
        assert "build the app from the plan" in summary["instructions"]
        work.assert_not_awaited()
        await provider.inbox.put(
            {
                "type": "response.created",
                "response": {"id": "recap", "metadata": summary["metadata"]},
            }
        )
        await provider.inbox.put(
            {
                "type": "response.output_audio.delta",
                "response_id": "recap",
                "delta": "AAE=",
            }
        )
        assert await event(browser.outbox) == b"\x00\x01"
        await provider.inbox.put(
            {
                "type": "response.done",
                "response": {"id": "recap", "status": "completed"},
            }
        )
        await relay.pending
        work.assert_awaited_once_with(
            scope, "build-once", "Go ahead and build the app plan."
        )
    finally:
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("admitted", [True, False])
async def test_plan_followthrough_supersedes_only_exact_old_audio_after_durable_admission(
    monkeypatch, admitted
):
    scope = native_voice.VoiceScope("caller", "workspace", "", "thread")
    browser, provider = Browser(), Provider()

    async def delegate(scope, call_id, request, **kwargs):
        if not admitted:
            raise RuntimeError("admission unavailable")
        receipt = SimpleNamespace(work_item_id="build-work", message_id="build-msg")
        kwargs["on_persisted"](receipt)
        await kwargs["on_accepted"](receipt, "running", None)
        return None

    monkeypatch.setattr(native_voice, "delegate", delegate)
    relay = native_voice.VoiceRelay(
        browser, provider, scope, check=AsyncMock(), work=delegate
    )
    relay.ready = True
    old = native_voice._AcceptedVoiceTurn("old-plan", "old-msg", "Plan a new app")
    unrelated = native_voice._AcceptedVoiceTurn(
        "other", "other-msg", "Summarize invoices"
    )
    relay.accepted = old
    relay.contextualized_results.add(old.work_item_id)
    relay.result_receipts[old.work_item_id] = old
    relay.result_receipts[unrelated.work_item_id] = unrelated
    await relay._start_requested_work(
        "build", "Go ahead and build the app plan.", "request"
    )
    await relay.pending
    assert ("old-plan" not in relay.result_receipts) is admitted
    assert relay.result_receipts["other"] is unrelated
    assert relay.protocol_counts["prior_plan_readout_superseded"] == int(admitted)
