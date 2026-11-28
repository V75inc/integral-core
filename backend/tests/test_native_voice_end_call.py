"""Explicit call termination is a presentation control, never an operation on work."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agentive.api import native_voice as endpoint
from app.agentive.services import work_items
from app.api.errors import InsufficientPermissionsError
from app.services import chat_turn_submissions, native_voice
from tests.test_native_voice import Browser, Provider, event, request
from tests.test_native_voice_completion import _send_tool
from tests.test_native_voice_endpoint_delivery import _EndpointBrowser, _patch_endpoint


@pytest.mark.parametrize(
    "text",
    [
        "hang up",
        "Please hang up now.",
        "Could you end this voice call, please?",
        "Okay, thanks, you can disconnect now",
        "Let's end the call",
        "I'd like you to close this live voice call",
        "You can disconnect.",
    ],
)
def test_explicit_ending_clauses(text):
    assert native_voice._explicit_end_call(text)


@pytest.mark.parametrize(
    "text",
    [
        "Goodbye",
        "Thanks",
        "That's all",
        "Don't hang up",
        "Hang up after the report finishes",
        "Tell me how to end the call",
        "Cancel the current work",
        "Stop speaking",
        "If I say hang up",
        "Yes",
        "Disconnect the Gmail connector",
        "Do not end the call",
        "Please hang up? No, wait.",
    ],
)
def test_ambiguity_negation_and_other_actions_are_not_end_call(text):
    assert not native_voice._explicit_end_call(text)


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["", "published-profile"])
async def test_end_call_capability_is_transport_scoped_and_rejects_ambiguous_intent(
    profile,
):
    config = native_voice.session_update("", uses_published_profile=bool(profile))[
        "session"
    ]
    assert native_voice.END_CALL in {tool["name"] for tool in config["tools"]}
    unavailable = native_voice.session_update(
        "",
        actions=native_voice.VoiceCallActions(),
        uses_published_profile=bool(profile),
    )["session"]
    assert native_voice.END_CALL not in {tool["name"] for tool in unavailable["tools"]}
    scope = native_voice.VoiceScope("caller", "workspace", profile, "thread")
    relay = native_voice.VoiceRelay(Browser(), Provider(), scope, check=AsyncMock())
    relay.ready = True
    relay.user_spoke = True
    relay.response_generation = "turn"
    relay._speak_result = AsyncMock()
    await relay._completed_response(
        request(
            "ambiguous",
            "turn",
            "",
            name=native_voice.END_CALL,
            args={"request": "Goodbye"},
        )
    )
    assert not relay.end_requested.is_set()
    assert not relay.closing
    relay._speak_result.assert_awaited_once()


@pytest.mark.asyncio
async def test_end_call_cannot_be_requested_by_unsolicited_or_revoked_response():
    scope = native_voice.VoiceScope("caller", "workspace", "", "thread")
    check = AsyncMock()
    relay = native_voice.VoiceRelay(Browser(), Provider(), scope, check=check)
    relay.ready = True
    relay.response_generation = "turn"
    relay._speak_result = AsyncMock()
    await relay._completed_response(
        request(
            "unsolicited",
            "turn",
            "",
            name=native_voice.END_CALL,
            args={"request": "Hang up"},
        )
    )
    assert not relay.end_requested.is_set()
    relay.user_spoke = True
    relay.response_generation = "turn-two"
    check.side_effect = InsufficientPermissionsError(message="Revoked")
    with pytest.raises(InsufficientPermissionsError):
        await relay._completed_response(
            request(
                "revoked",
                "turn-two",
                "",
                name=native_voice.END_CALL,
                args={"request": "Hang up"},
            )
        )
    assert not relay.end_requested.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("farewell_available", [True, False])
async def test_spoken_hangup_closes_actual_endpoint_and_preserves_admitted_work(
    monkeypatch, farewell_available
):
    """Exercise the API lifetime, including a missing synthesis or browser disconnect."""
    browser, provider = _EndpointBrowser(), Provider()
    _patch_endpoint(monkeypatch, provider)
    submit = AsyncMock(
        return_value=SimpleNamespace(
            work_item_id="resident-work", message_id="accepted"
        )
    )
    monkeypatch.setattr(chat_turn_submissions, "submit_chat_turn", submit)
    monkeypatch.setattr(
        native_voice, "_delegated_status", AsyncMock(return_value=("running", None))
    )
    monkeypatch.setattr(native_voice, "END_FAREWELL_SECONDS", 0.05)
    monkeypatch.setattr(native_voice, "END_PLAYBACK_SECONDS", 0.05)
    cancel = AsyncMock()
    monkeypatch.setattr(work_items, "cancel_work_item", cancel)
    task = asyncio.create_task(endpoint.native_voice(browser))
    try:
        await event(provider.outbox)
        await provider.inbox.put({"type": "session.updated"})
        assert (await event(browser.outbox))["type"] == "ready"
        await _send_tool(provider, "submit", "work")
        assert "accepted" in (await event(provider.outbox))["item"]["output"]
        await provider.inbox.put({"type": "input_audio_buffer.speech_started"})
        assert await event(browser.outbox) == {"type": "flush"}
        assert (await event(provider.outbox))["type"] == "session.update"
        await provider.inbox.put({"type": "input_audio_buffer.speech_stopped"})
        await provider.inbox.put(
            {"type": "response.created", "response": {"id": "hangup"}}
        )
        await provider.inbox.put(
            request(
                "hangup",
                "hangup",
                "",
                name=native_voice.END_CALL,
                args={"request": "Please hang up now"},
            )
        )
        assert "Ending this call" in (await event(provider.outbox))["item"]["output"]
        assert await event(browser.outbox) == {"type": "call_ending"}
        assert await event(browser.outbox) == {"type": "flush"}
        farewell = await event(provider.outbox)
        assert "Take care." in farewell["response"]["instructions"]
        assert farewell["response"]["conversation"] == "none"
        if farewell_available:
            await provider.inbox.put(
                {
                    "type": "response.created",
                    "response": {
                        "id": "farewell",
                        "metadata": farewell["response"]["metadata"],
                    },
                }
            )
            await provider.inbox.put(
                {
                    "type": "response.output_audio.delta",
                    "response_id": "farewell",
                    "delta": "AAE=",
                }
            )
            assert await event(browser.outbox) == b"\x00\x01"
            await provider.inbox.put(
                {
                    "type": "response.done",
                    "response": {"id": "farewell", "status": "completed"},
                }
            )
        assert await event(browser.outbox) == {"type": "call_ended"}
        if farewell_available:
            await browser.inbox.put({"type": "websocket.disconnect"})
        await asyncio.wait_for(task, 2)
        provider.close.assert_awaited_once()
        assert "caller" not in endpoint._active_calls
        submit.assert_awaited_once()
        cancel.assert_not_awaited()
        assert not task.exception()
    finally:
        if not task.done():
            await browser.inbox.put({"type": "websocket.disconnect"})
            await task


@pytest.mark.asyncio
async def test_record_spoken_projection_preserves_source_facts_and_exact_readback():
    scope = native_voice.VoiceScope("caller", "workspace", "", "thread")
    provider = Provider()
    relay = native_voice.VoiceRelay(Browser(), provider, scope, check=AsyncMock())
    record = '{"title":"Invoice A","reference":"00981","amount":127.50,"currency":"USD","due":"2026-12-03","status":"Open","notes":null}'
    await relay._speak_result(0, result=record)
    speech = (await event(provider.outbox))["response"]["instructions"]
    assert "fluent spoken sentences" in speech
    assert "raw JSON" in speech
    assert "significant leading zeroes" in speech
    assert "include every requested field" in speech
    assert "00981" in speech and "127.50" in speech and "2026-12-03" in speech
    relay._discard_silent_readout()
    await relay._speak_result(0, result=record, read_back=True)
    exact = (await event(provider.outbox))["response"]["instructions"]
    assert "Read the supplied message aloud once" in exact
    assert "fluent spoken sentences" not in exact


@pytest.mark.asyncio
async def test_end_call_remains_available_after_workspace_tool_budget():
    browser, provider = Browser(), Provider()
    relay = native_voice.VoiceRelay(
        browser,
        provider,
        native_voice.VoiceScope("caller", "workspace", "", "thread"),
        check=AsyncMock(),
    )
    relay.ready = True
    relay.seen = {f"tool-{index}" for index in range(native_voice.MAX_DELEGATIONS)}
    relay.response_generation = "exhausted"
    relay._speak_result = AsyncMock()
    await relay._completed_response(
        request("extra-work", "exhausted", "Read another entry")
    )
    assert "tool limit" in (await event(provider.outbox))["item"]["output"]
    session = (await event(provider.outbox))["session"]
    assert [tool["name"] for tool in session["tools"]] == [native_voice.END_CALL]
    relay.response_generation = "caller-end"
    relay.end_intent_response = "caller-end"
    relay.user_spoke = True
    await relay._completed_response(
        request(
            "hangup",
            "caller-end",
            "",
            name=native_voice.END_CALL,
            args={"request": "End the call"},
        )
    )
    assert "Ending this call" in (await event(provider.outbox))["item"]["output"]
    assert relay.end_requested.is_set()


@pytest.mark.asyncio
async def test_later_unsolicited_response_cannot_hang_up_after_a_real_turn():
    browser, provider = Browser(), Provider()
    relay = native_voice.VoiceRelay(
        browser,
        provider,
        native_voice.VoiceScope("caller", "workspace", "", "thread"),
        check=AsyncMock(),
    )
    relay.ready = True
    reader = asyncio.create_task(relay._read_provider())
    try:
        await provider.inbox.put({"type": "input_audio_buffer.speech_started"})
        assert await event(browser.outbox) == {"type": "flush"}
        await provider.inbox.put({"type": "input_audio_buffer.speech_stopped"})
        await provider.inbox.put(
            {"type": "response.created", "response": {"id": "ordinary"}}
        )
        await provider.inbox.put(
            {
                "type": "response.done",
                "response": {"id": "ordinary", "status": "completed"},
            }
        )
        await provider.inbox.put(
            {"type": "response.created", "response": {"id": "later"}}
        )
        await provider.inbox.put(
            request(
                "unsolicited",
                "later",
                "",
                name=native_voice.END_CALL,
                args={"request": "Hang up"},
            )
        )
        assert "please ask me" in (await event(provider.outbox))["item"]["output"]
        assert not relay.end_requested.is_set()
        assert not reader.done()
    finally:
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)


@pytest.mark.asyncio
async def test_hangup_from_caller_when_provider_response_precedes_vad_stop():
    """A real caller turn may be announced by the provider before speech_stopped."""
    browser, provider = Browser(), Provider()
    relay = native_voice.VoiceRelay(
        browser,
        provider,
        native_voice.VoiceScope("caller", "workspace", "", "thread"),
        check=AsyncMock(),
    )
    relay.ready = True
    reader = asyncio.create_task(relay._read_provider())
    try:
        await provider.inbox.put({"type": "input_audio_buffer.speech_started"})
        assert await event(browser.outbox) == {"type": "flush"}
        await provider.inbox.put(
            {"type": "response.created", "response": {"id": "ending"}}
        )
        await provider.inbox.put({"type": "input_audio_buffer.speech_stopped"})
        await provider.inbox.put(
            request(
                "end",
                "ending",
                "",
                name=native_voice.END_CALL,
                args={"request": "Could we end this call now?"},
            )
        )
        assert "Ending this call" in (await event(provider.outbox))["item"]["output"]
        await asyncio.wait_for(relay.end_requested.wait(), 1)
        assert relay.protocol_counts["explicit_call_end"] == 1
    finally:
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)
