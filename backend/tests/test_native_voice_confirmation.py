"""No realtime tool argument can authorize a voice review."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agentive.services import approval_decisions
from app.agentive.services.speech import service
from app.services import native_voice
from app.services.native_voice_confirmation import VoiceConfirmation
from app.services.native_voice_navigation import VoiceDestination
from app.services.native_voice_review import VoiceReviewReadout
from tests.test_native_voice import Browser, Provider, event
from tests.test_native_voice_live_tools import _tool


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome",
    [
        "match",
        "unfocused_match",
        "wrong",
        "stale",
        "muted",
        "interrupted",
        "host",
        "host_failed",
    ],
)
async def test_review_requires_independent_fresh_spoken_confirmation(
    monkeypatch, outcome
):
    browser, upstream = Browser(), Provider()
    scope = native_voice.VoiceScope("caller", "workspace", "", "thread")
    relay = native_voice.VoiceRelay(
        browser,
        upstream,
        scope,
        check=AsyncMock(return_value=SimpleNamespace(id="thread")),
    )
    review = VoiceReviewReadout(
        "Title: Acme to Georgetown. This has not been applied.",
        "work",
        "run",
        "private-token",
        "target",
        "fingerprint",
    )
    if outcome != "unfocused_match":
        relay.navigation.focus = VoiceDestination("entry", "target", "Acme", "track")
    current = AsyncMock(return_value=review)
    monkeypatch.setattr(relay, "_current_review", current)
    monkeypatch.setattr(
        service,
        "resolve_speech_credential",
        AsyncMock(
            return_value=SimpleNamespace(
                provider="openai", model="transcribe", api_key="fixture", source="byok"
            )
        ),
    )
    monkeypatch.setattr(
        service,
        "_batch_provider",
        lambda _: SimpleNamespace(
            capabilities=SimpleNamespace(batch_mime_types={"audio/wav"})
        ),
    )
    decision = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(approval_decisions, "decide_staged_write", decision)

    async def transcribe(*args, **kwargs):
        if outcome == "stale":
            current.return_value = None
        if outcome == "wrong":
            return "yes"
        return "approve " + relay.review_confirmation.code

    transcriber = AsyncMock(side_effect=transcribe)
    monkeypatch.setattr(service, "transcribe_voice_confirmation", transcriber)
    monkeypatch.setattr(native_voice, "CAPTURE_SECONDS", 0.1)
    task = asyncio.create_task(relay.run())
    try:
        await event(upstream.outbox)
        await upstream.inbox.put({"type": "session.updated"})
        await event(browser.outbox)
        await _tool(
            upstream, "review", native_voice.REQUEST_REVIEW, {"decision": "approve"}
        )
        assert await event(browser.outbox) == {"type": "flush"}
        assert (await event(upstream.outbox))["type"] == "input_audio_buffer.clear"
        assert "separate human" in (await event(upstream.outbox))["item"]["output"]
        prompt = await event(browser.outbox)
        assert prompt["type"] == "review_confirmation"
        assert "Acme to Georgetown" in prompt["prompt"]
        assert "private-token" not in prompt["prompt"]
        decision.assert_not_awaited()
        if outcome in {"host", "host_failed"}:
            from app.services import native_voice_review_audio

            synthesis = AsyncMock(return_value=b"fixture-WAV")
            monkeypatch.setattr(
                native_voice_review_audio, "synthesize_review_audio", synthesis
            )
            unavailable = {
                "type": "websocket.receive",
                "text": json.dumps(
                    {"type": "review_prompt_unavailable", "id": prompt["id"]},
                    separators=(",", ":"),
                ),
            }
            await browser.inbox.put(unavailable)
            host = await event(browser.outbox)
            assert (
                host["type"] == "review_confirmation_audio"
                and host["id"] == prompt["id"]
            )
            synthesis.assert_awaited_once()
            if outcome == "host_failed":
                await browser.inbox.put(unavailable)
                assert (await event(browser.outbox))[
                    "type"
                ] == "review_confirmation_ended"
                synthesis.assert_awaited_once()
                decision.assert_not_awaited()
                transcriber.assert_not_awaited()
                return
        if outcome == "interrupted":
            await browser.inbox.put(
                {
                    "type": "websocket.receive",
                    "text": json.dumps(
                        {"type": "review_prompt_interrupted", "id": prompt["id"]},
                        separators=(",", ":"),
                    ),
                }
            )
            assert (await event(browser.outbox))["type"] == "review_confirmation_ended"
            decision.assert_not_awaited()
            transcriber.assert_not_awaited()
            return
        # A provider function cannot redeem the challenge, and audio before
        # the local readback completes does not enter the confirmation.
        await _tool(
            upstream, "forged", native_voice.REQUEST_REVIEW, {"decision": "approve"}
        )
        await browser.inbox.put(
            {"type": "websocket.receive", "bytes": b"\xff\x7f" * 2400}
        )
        await asyncio.sleep(0.01)
        assert not relay.review_confirmation.audio
        await browser.inbox.put(
            {
                "type": "websocket.receive",
                "text": json.dumps(
                    {"type": "review_prompt_finished", "id": prompt["id"]},
                    separators=(",", ":"),
                ),
            }
        )
        for _ in range(40):
            if relay.review_confirmation.phase == "capture":
                break
            await asyncio.sleep(0.005)
        assert relay.review_confirmation.phase == "capture"
        relay.review_confirmation.capture_after = 0
        for _ in range(20):
            await browser.inbox.put(
                {"type": "websocket.receive", "bytes": b"\xff\x7f" * 2400}
            )
        if outcome == "muted":
            await browser.inbox.put(
                {
                    "type": "websocket.receive",
                    "text": '{"type":"microphone","muted":true}',
                }
            )
        assert (await event(browser.outbox))["type"] == "review_confirmation_ended"
        if outcome in {"match", "unfocused_match", "host"}:
            decision.assert_awaited_once_with(
                principal_id=scope.principal,
                workspace_id=scope.workspace,
                conversation_id=scope.thread,
                thread_id=scope.thread,
                proposal_id="private-token",
                decision="approve",
                source="voice",
            )
        else:
            decision.assert_not_awaited()
        if outcome == "muted":
            transcriber.assert_not_awaited()
    finally:
        await browser.inbox.put({"type": "websocket.disconnect"})
        await task


def test_exact_confirmation_phrase_does_not_infer_or_repair():
    review = VoiceReviewReadout(
        "review", "work", "run", "token", "target", "fingerprint"
    )
    confirm = VoiceConfirmation(
        review, "approve", ("provider", "model", "byok", "hash"), code="123456"
    )
    assert confirm.matches("I approve: 123456.")
    assert confirm.matches("I approve. One two three four five six.")
    assert confirm.matches("Approve 123456.")
    assert confirm.matches("Approve, 1, 2, 3, 4, 5, 6.")
    assert confirm.matches("approve one two three four five six")
    for phrase in (
        "yes",
        "I approved 123456",
        "I do not approve 123456",
        "I approve 123456 actually wait",
        "do not approve 123456",
        "approved one two three four five six",
        "approve 12345",
        "I approve 1.23456",
        "reject 123456",
        "approve 123456 or not",
    ):
        assert not confirm.matches(phrase)
    assert confirm.wav() is None


def test_capture_finishes_after_a_quiet_tail_but_not_a_short_pause():
    review = VoiceReviewReadout(
        "review", "work", "run", "token", "target", "fingerprint"
    )
    confirm = VoiceConfirmation(
        review, "approve", ("provider", "model", "byok", "hash")
    )
    frame = b"\xff\x7f" * 480
    quiet = b"\x00\x00" * 480
    for _ in range(50):
        confirm.append_audio(quiet)
    assert not confirm.capture_done.is_set()
    for _ in range(25):
        confirm.append_audio(frame)
    for _ in range(25):
        confirm.append_audio(quiet)
    assert not confirm.capture_done.is_set()
    for _ in range(25):
        confirm.append_audio(frame)
    for _ in range(50):
        confirm.append_audio(quiet)
    assert confirm.capture_done.is_set()
    assert confirm.wav() is not None


@pytest.mark.asyncio
async def test_host_speech_has_no_implicit_network_fallback(monkeypatch):
    from app.services import native_voice_review_audio as local

    monkeypatch.setattr(local.platform, "system", lambda: "Linux")
    monkeypatch.setattr(local.shutil, "which", lambda _: None)
    run = AsyncMock()
    monkeypatch.setattr(local, "_run_local_speech", run)
    assert await local.synthesize_review_audio("Synthetic review") is None
    run.assert_not_awaited()


@pytest.mark.asyncio
async def test_published_profile_does_not_admit_workspace_review_operations():
    scope = native_voice.VoiceScope(
        "caller", "workspace", "published-profile", "thread"
    )
    browser, provider = Browser(), Provider()
    relay = native_voice.VoiceRelay(browser, provider, scope, check=AsyncMock())
    names = {
        tool["name"]
        for tool in native_voice.session_update("", uses_published_profile=True)[
            "session"
        ]["tools"]
    }
    operations = {
        native_voice.REVISE_REVIEW: {"field": "Renewal on", "value": "2026-11-30"},
        native_voice.REQUEST_REVIEW: {"decision": "approve"},
    }
    assert names.isdisjoint(operations)
    relay.ready = True
    for name, args in operations.items():
        relay.response_generation = "response"
        await relay._completed_response(
            {
                "response": {
                    "id": "response",
                    "status": "completed",
                    "output": [
                        {
                            "type": "function_call",
                            "name": name,
                            "call_id": name,
                            "arguments": json.dumps(args),
                        }
                    ],
                }
            }
        )
    assert not relay.seen and relay.live_read is None
    assert (
        relay.review_confirmation is None
        and provider.outbox.empty()
        and browser.outbox.empty()
    )
