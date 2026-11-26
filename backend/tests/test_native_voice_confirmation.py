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
        "decline",
        "wrong",
        "stale",
        "muted",
        "interrupted",
        "playback_failed",
    ],
)
async def test_review_requires_independent_fresh_spoken_confirmation(
    monkeypatch, outcome
):
    """Only the current post-playback microphone answer can decide a proposal."""
    browser, upstream = Browser(), Provider()
    scope = native_voice.VoiceScope("caller", "workspace", "", "thread")
    relay = native_voice.VoiceRelay(
        browser,
        upstream,
        scope,
        check=AsyncMock(return_value=SimpleNamespace(id="thread")),
        review_voice=SimpleNamespace(render=AsyncMock(return_value=b"fixture-WAV")),
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

    async def transcribe(*args: object, **kwargs: object):
        if outcome == "stale":
            current.return_value = None
        if outcome == "decline":
            return "No."
        if outcome == "wrong":
            return "Yes, but make it tomorrow."
        return "Yes, go ahead."

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
        assert "prompt" not in prompt
        audio = await event(browser.outbox)
        assert (
            audio["type"] == "review_confirmation_audio" and audio["id"] == prompt["id"]
        )
        assert "Acme to Georgetown" in relay.review_confirmation.review.text
        decision.assert_not_awaited()
        if outcome == "playback_failed":
            await browser.inbox.put(
                {
                    "type": "websocket.receive",
                    "text": json.dumps(
                        {"type": "review_prompt_unavailable", "id": prompt["id"]},
                        separators=(",", ":"),
                    ),
                }
            )
            assert (await event(browser.outbox))["type"] == "review_confirmation_ended"
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
        # A provider function cannot answer the question, and audio before
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
        if outcome in {"match", "unfocused_match", "decline"}:
            decision.assert_awaited_once_with(
                principal_id=scope.principal,
                workspace_id=scope.workspace,
                conversation_id=scope.thread,
                thread_id=scope.thread,
                proposal_id="private-token",
                decision="reject" if outcome == "decline" else "approve",
                source="voice",
            )
        else:
            decision.assert_not_awaited()
        if outcome == "muted":
            transcriber.assert_not_awaited()
    finally:
        await browser.inbox.put({"type": "websocket.disconnect"})
        await task


def test_capture_finishes_after_a_quiet_tail_but_not_a_short_pause():
    """Keep both parts of a reply across a short pause."""
    review = VoiceReviewReadout(
        "review", "work", "run", "token", "target", "fingerprint"
    )
    confirm = VoiceConfirmation(review, ("provider", "model", "byok", "hash"))
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
async def test_renderer_reads_one_exact_question_in_the_call_voice(monkeypatch):
    """Send only the reviewed question for synthesis and play the returned audio."""
    import wave
    from io import BytesIO

    from app.services import native_voice_review_audio as renderer

    clips = AsyncMock(return_value={"prompt": bytes(480)})
    monkeypatch.setattr(renderer, "_clips", clips)
    socket = AsyncMock()
    socket.recv.return_value = '{"type":"session.updated"}'
    voice = renderer.ReviewVoice(AsyncMock(return_value=socket))
    confirmation = VoiceConfirmation(
        VoiceReviewReadout("set the label to blue", "w", "r", "t", "e", "f"), ()
    )
    with wave.open(BytesIO(await voice.render(confirmation))) as audio:
        assert audio.readframes(audio.getnframes()) == bytes(480)
    clips.assert_awaited_once_with(
        socket, {"prompt": "Would you like me to set the label to blue?"}
    )
    socket.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_published_profile_does_not_admit_workspace_review_operations():
    """Check both advertised tools and their runtime admission."""
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


@pytest.mark.parametrize(
    "heard,expected",
    [
        ("Go ahead and approve the update.", "approve"),
        ("Yes, please.", "approve"),
        ("Sure thing!", "approve"),
        ("Sounds good to me.", "approve"),
        ("Yeah, please do.", "approve"),
        ("Absolutely, go for it.", "approve"),
        ("Yes, I would.", "approve"),
        ("Let’s do it.", "approve"),
        ("No thanks.", "reject"),
        ("No, please don’t.", "reject"),
        ("I don’t approve it.", "reject"),
        ("I’d rather not.", "reject"),
        ("Don’t reject it.", None),
        ("Don't cancel the change.", None),
        ("Please don’t do that.", "reject"),
        ("I decline.", "reject"),
        ("Yes, but only if it's private.", None),
        ("No, actually yes.", None),
        ("She said yes.", None),
        ("Yes?", None),
        ("yes 123456", None),
        ("", None),
        ("No, reject it.", "reject"),
        ("Don't approve yet.", None),
        ("Approve it, actually no.", None),
        ("If I approve it, what happens?", None),
        ("Change the time to ten.", None),
    ],
)
def test_independent_review_question_routes_only_clear_intent(heard, expected):
    """Require one unqualified polarity across the complete answer."""
    from app.services.native_voice_confirmation import spoken_review_choice

    assert spoken_review_choice(heard) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phrase,choice",
    [
        ("go ahead and approve the update", "approve"),
        ("no, reject it", "reject"),
        ("don't approve yet", None),
    ],
)
async def test_one_spoken_answer_decides_the_offered_review(
    monkeypatch, phrase, choice
):
    """Handle a clear answer once without starting a second prompt."""
    browser, upstream = Browser(), Provider()
    relay = native_voice.VoiceRelay(
        browser,
        upstream,
        native_voice.VoiceScope("caller", "workspace", "", "thread"),
        check=AsyncMock(),
        conversational_review=True,
    )
    review = VoiceReviewReadout(
        "Create the record.", "work", "run", "proposal", "track", "fingerprint"
    )
    monkeypatch.setattr(relay, "_current_review", AsyncMock(return_value=review))
    offer = AsyncMock()
    monkeypatch.setattr(relay, "_request_review_confirmation", offer)
    monkeypatch.setattr(
        service, "transcribe_voice_confirmation", AsyncMock(return_value=phrase)
    )
    commit = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(approval_decisions, "decide_staged_write", commit)
    await relay._speak_result(
        relay.generation, navigation_readout=review, result=review.text, work_id="work"
    )
    offer.assert_awaited_once_with(None, "ask", relay.generation, expected=review)
    confirmation = VoiceConfirmation(
        review, ("provider", "model", "byok", "credential")
    )
    confirmation.phase = "capture"
    confirmation.audio.extend(b"\xff\x7f" * 48000)
    confirmation.capture_done.set()
    relay.review_confirmation = confirmation
    await relay._capture_review_confirmation(confirmation)
    assert offer.await_count == 1
    if choice:
        commit.assert_awaited_once_with(
            principal_id="caller",
            workspace_id="workspace",
            conversation_id="thread",
            thread_id="thread",
            proposal_id="proposal",
            decision=choice,
            source="voice",
        )
    else:
        commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("caption", "valid"),
    [
        ('"Move the balance from $10 to -$20."', True),
        ("Move the balance from $10 to $20.", False),
        ("Move the balance from $10 to -$200.", False),
        ("Move the balance from $10 to -$20. Done.", False),
    ],
)
async def test_review_caption_must_preserve_the_exact_proposal(caption, valid):
    """Allow typographic variations while preserving amounts and signs."""
    from app.services.native_voice_review_audio import _clips

    script = "Move the balance from $10 to -$20."
    socket = AsyncMock()
    events = [
        {
            "type": "response.created",
            "response": {"id": "r", "metadata": {"clip": "prompt"}},
        },
        {
            "type": "response.output_audio.delta",
            "response_id": "r",
            "delta": "AAAAAA==",
        },
        {
            "type": "response.done",
            "response": {
                "id": "r",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_audio", "transcript": caption}],
                    }
                ],
            },
        },
    ]
    socket.recv.side_effect = [json.dumps(event) for event in events]
    if valid:
        assert await _clips(socket, {"prompt": script}) == {"prompt": bytes(4)}
    else:
        with pytest.raises(ValueError, match="differed"):
            await _clips(socket, {"prompt": script})
    request = json.loads(socket.send.await_args.args[0])["response"]
    assert request["input"] == [] and request["conversation"] == "none"
    assert request["tools"] == [] and request["tool_choice"] == "none"


@pytest.mark.asyncio
async def test_a_changed_proposal_cannot_retry_a_misread_prompt(monkeypatch):
    """Drop the old readback when a proposal changes between attempts."""
    from app.services.native_voice_review_audio import InexactReadback

    review = VoiceReviewReadout(
        "New title.", "work", "run", "proposal", "target", "revision"
    )
    renderer = SimpleNamespace(render=AsyncMock(side_effect=InexactReadback()))
    relay = native_voice.VoiceRelay(
        Browser(),
        Provider(),
        native_voice.VoiceScope("caller", "workspace", "", "thread"),
        check=AsyncMock(),
        review_voice=renderer,
    )
    confirmation = VoiceConfirmation(review, ())
    relay.review_confirmation = confirmation
    monkeypatch.setattr(relay, "_current_review", AsyncMock(side_effect=[review, None]))
    monkeypatch.setattr(relay, "_finish_confirmation", AsyncMock())
    await relay._render_review_prompt(confirmation)
    renderer.render.assert_awaited_once_with(confirmation)
    relay._finish_confirmation.assert_awaited_once()
    assert relay.browser.outbox.empty()
    assert not confirmation.audio


def test_short_spoken_answer_survives_capture_but_a_transient_does_not():
    """Distinguish a brief answer from an isolated microphone transient."""
    review = VoiceReviewReadout("Add a record.", "work", "run", "id", "target", "rev")
    for duration, expected in [(0.12, True), (0.005, False)]:
        capture = VoiceConfirmation(review, ())
        capture.append_audio(b"\xff\x7f" * int(24000 * duration))
        capture.append_audio(b"\x00\x00" * 24000)
        assert capture.capture_done.is_set() is expected
        assert (capture.wav() is not None) is expected


def test_local_greeting_uses_account_zone_and_never_invents_a_location():
    """Resolve the greeting from an explicit account timezone."""
    from datetime import datetime, timezone

    from app.services.user_timezone import greeting_period

    instant = datetime(2026, 11, 26, 10, 30, tzinfo=timezone.utc)
    assert greeting_period("America/Guyana", instant) == "morning"
    assert greeting_period("Asia/Tokyo", instant) == "evening"
    assert greeting_period(None, instant) == ""
    assert greeting_period("Invalid/Timezone", instant) == ""
    assert (
        "It is morning"
        in native_voice._opening_response("Eve", "new", "morning")["response"][
            "instructions"
        ]
    )


def test_legacy_voice_list_is_not_resident_permission():
    """Accept old opening frames without deriving authority from their track."""
    from app.schemas.voice_configuration import VoiceStart
    from app.services.native_voice import VoiceScope

    opening = VoiceStart(
        ticket="a" * 43, workspace_id="workspace", direct_track="n.Track.old"
    )
    assert opening.direct_track
    assert "direct_track" not in VoiceScope.__dataclass_fields__


@pytest.mark.asyncio
@pytest.mark.parametrize("intent", ["details", "hold", "clarify", "stale"])
async def test_informative_reply_never_decides_or_revises_a_changed_review(
    monkeypatch, intent
):
    """Discussion and an intervening change cannot authorize the pending proposal."""
    from app.services import user_timezone
    from app.services.native_voice_review_audio import ReviewFollowup

    review = VoiceReviewReadout(
        "add “Cedar” to Tasks", "w", "r", "p", "t", "f", (("title", "Title"),)
    )
    current = AsyncMock(return_value=review)

    async def interpret(*_args: object):
        if intent == "stale":
            current.return_value = None
            return ReviewFollowup(intent="revise", field="Title", value="Elm")
        return ReviewFollowup(intent=intent)

    relay = native_voice.VoiceRelay(
        Browser(),
        Provider(),
        native_voice.VoiceScope("human", "ws", "", "thread"),
        check=AsyncMock(),
        review_voice=SimpleNamespace(interpret_reply=interpret),
        conversational_review=True,
    )
    monkeypatch.setattr(
        user_timezone, "user_time_context", AsyncMock(return_value="Thursday")
    )
    monkeypatch.setattr(relay, "_current_review", current)
    offer, revise, speak, commit = (AsyncMock() for _ in range(4))
    monkeypatch.setattr(relay, "_request_review_confirmation", offer)
    monkeypatch.setattr(relay, "_revise_pending_review", revise)
    monkeypatch.setattr(relay, "_speak_result", speak)
    monkeypatch.setattr(approval_decisions, "decide_staged_write", commit)
    capture = VoiceConfirmation(review, ())
    relay.review_confirmation = capture
    await relay._follow_review_reply(capture, "What are the details?")
    if intent == "details":
        offer.assert_awaited_once_with(None, "ask", relay.generation, expected=review)
    else:
        offer.assert_not_awaited()
    if intent in {"hold", "clarify"}:
        speak.assert_awaited_once()
    revise.assert_not_awaited()
    commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_interpreter_cannot_supply_approval_or_access_call_tools():
    """Constrain the configured voice model to discussion of this one review."""
    from pydantic import ValidationError

    from app.services.native_voice_review_audio import ReviewVoice

    socket = AsyncMock()
    socket.recv.return_value = json.dumps(
        {
            "type": "response.done",
            "response": {
                "status": "completed",
                "output": [
                    {
                        "type": "function_call",
                        "name": "describe_review_followup",
                        "arguments": json.dumps({"intent": "approve"}),
                    }
                ],
            },
        }
    )
    review = VoiceReviewReadout(
        "add “Cedar” to Tasks", "w", "r", "secret-proposal-id", "t", "f"
    )
    with pytest.raises(ValidationError):
        await ReviewVoice(AsyncMock(return_value=socket)).interpret_reply(
            review, "Only if it's private.", "Thursday"
        )
    request = json.loads(socket.send.await_args.args[0])["response"]
    assert request["conversation"] == "none" and request["output_modalities"] == [
        "text"
    ]
    assert [tool["name"] for tool in request["tools"]] == ["describe_review_followup"]
    assert "secret-proposal-id" not in socket.send.await_args.args[0]
    socket.close.assert_awaited_once()


def test_review_interpreter_can_extract_literal_names_but_cannot_author_them():
    """Preserve dictated titles and reject a suggestion that names another field."""
    from app.services.native_voice_review_audio import ReviewFollowup

    review = VoiceReviewReadout(
        "rename Cedar", "w", "r", "p", "t", "f", (("title", "Title"),)
    )
    heard = "Actually, call it Golden Compass."
    followup = ReviewFollowup(intent="revise", field="Title", value="Golden Compass")
    assert followup.correction(review, heard) == {
        "field": "Title",
        "value": "Golden Compass",
    }
    assert followup.correction(review, "I have a different name in mind.") is None
    assert (
        followup.model_copy(update={"field": "Owner"}).correction(review, heard) is None
    )
