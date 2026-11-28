"""Both official voice protocols use distinct wire contracts, without network calls."""

import asyncio
import base64
import json
from types import SimpleNamespace
from typing import get_args
from unittest.mock import AsyncMock

import pytest

from app.schemas.voice_configuration import RealtimeVoiceModel, RealtimeVoiceModelWrite
from app.services.live_caller_turns import LiveCallerTurns
from app.services.native_live_voice_host import NativeLiveVoiceHost
from app.services.native_live_voice_review_audio import LiveReviewVoice
from app.services.native_voice import VoiceScope
from app.services.native_voice_review_audio import InexactReadback
from app.services.openai_voice_protocol import (
    LIVE_MODELS,
    REALTIME_MODELS,
    CompletedCallerTurn,
    LiveAppendAccepted,
    LiveAudio,
    LiveClosed,
    LiveDelegation,
    LiveProtocolError,
    LiveTranscript,
    LiveTaskRequest,
    LiveTurnLedger,
    OpenAILiveSession,
    VoiceWireProtocol,
    connect_voice_socket,
    model_protocol,
)


class FakeSocket:
    """Scripted server events and captured client frames."""

    def __init__(self):
        self.incoming = asyncio.Queue()
        self.sent = []
        self.closed = 0

    async def send(self, message):
        self.sent.append(json.loads(message))

    async def recv(self):
        return json.dumps(await self.incoming.get())

    async def close(self):
        self.closed += 1

    def enqueue(self, event):
        self.incoming.put_nowait(event)


def started(model="gpt-live-1.1", **changes):
    return {
        "type": "session.started",
        "session": {
            "id": "live-synthetic",
            "model": model,
            "audio": {"format": {"type": "audio/pcm", "rate": 24000}},
            "delegation": {"type": "client"},
            **changes,
        },
    }


def test_conversational_catalog_is_exact_and_realtime_config_matches():
    """Do not confuse translation, transcription, unknown IDs or Live with Realtime."""
    assert set(get_args(RealtimeVoiceModel)) - {""} == REALTIME_MODELS | LIVE_MODELS
    for model in REALTIME_MODELS:
        assert model_protocol(model) == VoiceWireProtocol.REALTIME
        assert (
            RealtimeVoiceModelWrite(
                mode="provider", connection_id="qa", model=model, revision=0
            ).model
            == model
        )
    for model in LIVE_MODELS:
        assert model_protocol(model) == VoiceWireProtocol.LIVE
    assert (
        RealtimeVoiceModelWrite(
            mode="provider", connection_id="qa", model="gpt-live-1", revision=0
        ).model
        == "gpt-live-1"
    )
    assert (
        RealtimeVoiceModelWrite(
            mode="provider", connection_id="qa", model="gpt-live-1.1", revision=0
        ).model
        == "gpt-live-1.1"
    )
    with pytest.raises(ValueError):
        RealtimeVoiceModelWrite(
            mode="provider", connection_id="qa", model="gpt-live-1-mini", revision=0
        )
    for model in (
        "gpt-live-1-mini",
        "gpt-realtime-whisper",
        "gpt-realtime-translate",
        "gpt-live-transcribe",
        "gpt-6.1-sol",
    ):
        assert model_protocol(model) is None


def test_live_delegation_waits_for_completed_caller_turn_with_correction():
    """A mid-utterance delegation is not an actionable request or approval."""
    ledger = LiveTurnLedger()
    ledger.observe(LiveTranscript("user", "Add the meeting on Tuesday", 100, 1500, "a"))
    ledger.observe(LiveDelegation("task", 1550, "d"))
    # The caller continues after the provider's delegation signal.
    ledger.observe(LiveTranscript("user", ", actually Thursday.", 1650, 2400, "b"))
    ledger.observe(LiveTranscript("assistant", "Okay", 2450, 2500, "c"))
    assert (
        ledger.transcript_for(100, 2600)
        == "Add the meeting on Tuesday, actually Thursday."
    )
    turn = CompletedCallerTurn(
        "turn-1", 100, 2600, "Add the meeting on Tuesday, actually Thursday."
    )
    request = ledger.commit(turn)
    assert request.delegation_id == "task" and request.turn == turn
    assert ledger.commit(turn) is None
    ledger.observe(LiveDelegation("task", 1550, "replay"))
    ledger.observe(LiveTranscript("user", "Yes", 3000, 3200, "d"))
    # A conversational backchannel is not a delegated task or a review proof.
    assert ledger.commit(CompletedCallerTurn("turn-2", 3000, 3300, "Yes")) is None


def test_live_turn_boundary_never_slices_transcript_fragments():
    ledger = LiveTurnLedger()
    ledger.observe(LiveTranscript("user", "Not Tuesday, Thursday", 100, 2000, "a"))
    with pytest.raises(LiveProtocolError, match="crosses caller turn boundary"):
        ledger.transcript_for(100, 1000)


def test_live_pcm_turn_fence_preserves_late_correction_and_delayed_delegation():
    """Provider metadata alone does not finish a turn; actual PCM quiet does."""
    now = [0.0]
    tracker = LiveCallerTurns(clock=lambda: now[0])
    tracker.observe(LiveTranscript("user", "Add a meeting Tuesday", 100, 900, "t1"))
    tracker.observe(LiveDelegation("d1", 950, "d"))
    assert tracker.poll() is None  # No observed user audio.
    tracker.audio(b"\xff\x7f" * 5000)
    tracker.audio(b"\x00\x00" * 12000)
    now[0] = 0.6
    assert tracker.poll() is None  # Not enough quiet: speaker continues.
    tracker.audio(b"\xff\x7f" * 5000)
    tracker.observe(LiveTranscript("user", ", actually Thursday.", 1000, 1500, "t2"))
    tracker.audio(b"\x00\x00" * 36000)
    now[0] = 2.3
    turn = tracker.poll()
    assert turn is not None and turn.text == "Add a meeting Tuesday, actually Thursday."
    tracker.observe(LiveDelegation("d1", 950, "replayed"))
    assert tracker.poll() is None
    tracker.audio(b"\xff\x7f" * 5000)
    tracker.observe(LiveTranscript("user", "What is the deadline?", 3000, 3900, "t3"))
    tracker.audio(b"\x00\x00" * 36000)
    now[0] = 4.0
    assert tracker.poll() is None  # Wait briefly for the late delegation.
    tracker.observe(LiveDelegation("d2", 4200, "late"))
    next_turn = tracker.poll()
    assert next_turn is not None and next_turn.text == "What is the deadline?"
    assert next_turn.end_ms == 4200


@pytest.mark.asyncio
async def test_wire_routes_are_fixed_and_no_cross_protocol_fallback(monkeypatch):
    connect = AsyncMock(return_value=FakeSocket())
    monkeypatch.setattr("websockets.asyncio.client.connect", connect)
    await connect_voice_socket(
        "gpt-realtime-2.1", "qa-key", protocol=VoiceWireProtocol.REALTIME
    )
    assert connect.call_args.args == (
        "wss://api.openai.com/v1/realtime?model=gpt-realtime-2.1",
    )
    await connect_voice_socket(
        "gpt-live-1.1", "qa-key", protocol=VoiceWireProtocol.LIVE
    )
    assert connect.call_args.args == ("wss://api.openai.com/v1/live/sessions",)
    assert connect.call_args.kwargs["additional_headers"] == {
        "Authorization": "Bearer qa-key"
    }
    with pytest.raises(ValueError):
        await connect_voice_socket(
            "gpt-live-1.1", "qa-key", protocol=VoiceWireProtocol.REALTIME
        )
    assert connect.await_count == 2


@pytest.mark.asyncio
async def test_live_session_starts_before_audio_and_retains_distinct_event_semantics():
    socket = FakeSocket()
    session = OpenAILiveSession(socket, "gpt-live-1.1")
    with pytest.raises(LiveProtocolError):
        await session.append_audio(b"\x00\x01")
    socket.enqueue(started())
    await session.start(instructions="Delegate tasks to the host.", voice="marin")
    assert socket.sent[0]["type"] == "session.start"
    assert socket.sent[0]["session"]["delegation"] == {
        "type": "client",
        "acknowledgement_delay_ms": 800,
    }
    assert socket.sent[0]["session"]["store"] is False
    assert "tools" not in socket.sent[0]["session"]
    await session.append_audio(b"\x00\x01")
    assert socket.sent[-1]["type"] == "session.input_audio.append"
    await session.mute(True)
    await session.append_audio(b"\x00\x01")
    assert socket.sent[-1]["type"] == "session.input_audio.mute"
    await session.mute(False)
    assert socket.sent[-1]["type"] == "session.input_audio.unmute"
    fragment = {
        "type": "session.input_transcript.delta",
        "event_id": "fragment-1",
        "delta": "Please review",
        "start_ms": 100,
        "end_ms": 200,
    }
    socket.enqueue(fragment)
    observed = await session.receive()
    assert observed == LiveTranscript("user", "Please review", 100, 200, "fragment-1")
    socket.enqueue(fragment)
    assert await session.receive() is None
    delegation = {
        "type": "session.delegation.created",
        "event_id": "delegate-1",
        "offset_ms": 300,
        "delegation": {"id": "task-1", "type": "delegation", "target": "client"},
    }
    socket.enqueue(delegation)
    assert await session.receive() == LiveDelegation("task-1", 300, "delegate-1")
    socket.enqueue(delegation)
    assert await session.receive() is None
    await session.append_context(
        "The task is running.",
        delegation_id="task-1",
        spoken=False,
        event_id="update-1",
    )
    assert socket.sent[-1]["type"] == "session.thinking.append"
    await session.append_context(
        "Two records found.", delegation_id="task-1", spoken=True, event_id="update-2"
    )
    assert socket.sent[-1]["type"] == "session.commentary.append"
    socket.enqueue(
        {
            "type": "session.commentary.appended",
            "client_event_id": "update-2",
            "end_ms": 350,
        }
    )
    assert await session.receive() == LiveAppendAccepted("update-2", 350, True)
    socket.enqueue(
        {
            "type": "session.output_audio.delta",
            "delta": base64.b64encode(b"\x00\x01").decode(),
        }
    )
    assert await session.receive() == LiveAudio(b"\x00\x01")
    with pytest.raises(LiveProtocolError):
        await session.append_context(
            "Wrong task", delegation_id="other", spoken=True, event_id="update-3"
        )


@pytest.mark.asyncio
async def test_live_configuration_mismatch_fails_before_audio():
    socket = FakeSocket()
    socket.enqueue(started(delegation={"type": "responses"}))
    session = OpenAILiveSession(socket, "gpt-live-1.1")
    with pytest.raises(LiveProtocolError, match="configuration mismatch"):
        await session.start(instructions="Host delegation", voice="marin")
    assert socket.closed == 1
    assert not session.ready


@pytest.mark.asyncio
async def test_live_close_waits_for_finalization_and_is_idempotent():
    socket = FakeSocket()
    socket.enqueue(started())
    session = OpenAILiveSession(socket, "gpt-live-1.1")
    await session.start(instructions="Host delegation", voice="marin")
    reader = asyncio.create_task(session.receive())
    closer = asyncio.create_task(session.close(timeout=1))
    await asyncio.sleep(0)
    socket.enqueue(
        {
            "type": "session.closed",
            "session": {"id": "live-synthetic"},
            "reason": "close_requested",
            "usage": {"seconds": 2},
        }
    )
    assert await reader == LiveClosed("close_requested", {"seconds": 2})
    assert await closer
    assert await session.close()
    assert socket.closed == 1
    assert [frame["type"] for frame in socket.sent].count("session.close") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("verified", [True, False])
async def test_live_review_requires_actual_audio_verification(verified):
    """An exact provider transcript is not a completed or heard review."""

    class ClockedSocket(FakeSocket):
        def __init__(self):
            super().__init__()
            self.input_running = asyncio.Event()
            self.started = False

        async def send(self, message):
            await super().send(message)
            frame = self.sent[-1]
            if frame["type"] == "session.input_audio.append":
                assert not any(base64.b64decode(frame["audio"]))
                self.input_running.set()

        async def recv(self):
            if self.started:
                await self.input_running.wait()
            self.started = True
            return await super().recv()

    socket = ClockedSocket()
    socket.enqueue(started())
    from app.services.native_voice_review import (
        VoiceReviewReadout,
        spoken_review_action,
    )

    action = "rename “Sample Alpha” to “Sample Beta”"
    readout = VoiceReviewReadout(
        action,
        "work",
        "run",
        "proposal",
        "target",
        "fingerprint",
        speech_action=spoken_review_action(action, "Sample Alpha", "Sample Beta"),
    )
    script = "Would you like me to rename Sample Alpha to Sample Beta?"
    assert readout.text != script and readout.speech_text == script
    socket.enqueue(
        {
            "type": "session.output_transcript.delta",
            "event_id": "script",
            "delta": script,
            "start_ms": 100,
            "end_ms": 1500,
        }
    )
    socket.enqueue(
        {
            "type": "session.output_audio.delta",
            "delta": base64.b64encode(b"\xff\x7f" * 2400).decode(),
        }
    )
    socket.enqueue(
        {
            "type": "session.output_audio.delta",
            "delta": base64.b64encode(b"\x00\x00" * 24000).decode(),
        }
    )
    socket.enqueue(
        {
            "type": "session.closed",
            "session": {"id": "live-synthetic"},
            "reason": "close_requested",
        }
    )
    verifier = AsyncMock(
        return_value=script if verified else "Add the meeting on Tuesday"
    )
    renderer = LiveReviewVoice(
        AsyncMock(return_value=OpenAILiveSession(socket, "gpt-live-1.1")), verifier
    )
    confirmation = SimpleNamespace(review=readout)
    if verified:
        recording = await renderer.render(confirmation)
        assert recording.startswith(b"RIFF")
    else:
        with pytest.raises(InexactReadback, match="Independent Live"):
            await renderer.render(confirmation)
    assert verifier.await_count == 1
    assert verifier.call_args.args[0] is confirmation
    assert socket.closed == 1
    assert socket.input_running.is_set()
    initial = socket.sent[0]["session"]["input"][0]
    assert initial["role"] == "developer"
    assert initial["content"][0]["type"] == "input_text"
    assert not any(f["type"] == "session.commentary.append" for f in socket.sent)


@pytest.mark.asyncio
async def test_live_host_preserves_native_resident_admission_and_review_capture():
    """A provider event cannot run work or turn a backchannel into approval."""
    socket = FakeSocket()
    socket.enqueue(started())
    scope = VoiceScope("caller", "workspace", "", "thread")
    check = AsyncMock()
    submit = AsyncMock(return_value="Resident verified result")
    acknowledge = AsyncMock(return_value=True)
    host = NativeLiveVoiceHost(
        OpenAILiveSession(socket, "gpt-live-1.1"),
        scope,
        check=check,
        submit=submit,
        bind_thread=AsyncMock(),
        acknowledge=acknowledge,
    )
    await host.start(
        instructions="Delegate workspace tasks to Integral.", voice="marin"
    )
    await host.observe(LiveTranscript("user", "List the urgent items", 100, 900, "t1"))
    await host.observe(LiveDelegation("d1", 1000, "e1"))
    assert submit.await_count == 0
    turn = CompletedCallerTurn("turn-1", 100, 1200, "List the urgent items")
    assert await host.commit_completed_turn(turn) == "Resident verified result"
    submit.assert_awaited_once_with(scope, "d1", "List the urgent items")
    assert await host.commit_completed_turn(turn) is None
    host.review_capture = True
    await host.observe(LiveTranscript("user", "yes", 1300, 1500, "t2"))
    await host.observe(LiveDelegation("d2", 1600, "e2"))
    assert (
        await host.commit_completed_turn(
            CompletedCallerTurn("turn-2", 1300, 1700, "yes")
        )
        is None
    )
    assert submit.await_count == 1
    host.review_capture = False
    acknowledge.return_value = False
    await host.observe(LiveDelegation("d3", 2000, "e3"))
    assert (
        await host.commit_completed_turn(
            CompletedCallerTurn("turn-3", 1800, 2100, "Open the agenda")
        )
        is None
    )
    assert submit.await_count == 1


@pytest.mark.asyncio
async def test_live_host_hangup_uses_completed_turn_and_releases_transport():
    socket = FakeSocket()
    socket.enqueue(started())
    session = OpenAILiveSession(socket, "gpt-live-1.1")
    scope = VoiceScope("caller", "workspace", "", "thread")
    submit = AsyncMock()
    host = NativeLiveVoiceHost(
        session,
        scope,
        check=AsyncMock(),
        submit=submit,
        bind_thread=AsyncMock(),
        acknowledge=AsyncMock(return_value=True),
    )
    await host.start(instructions="Voice conversation", voice="marin")
    await host.observe(LiveTranscript("user", "please hang up", 100, 300, "t1"))
    assert not host.closing
    reader = asyncio.create_task(session.receive())
    closer = asyncio.create_task(
        host.commit_completed_turn(
            CompletedCallerTurn("turn-1", 100, 400, "please hang up")
        )
    )
    await asyncio.sleep(0)
    socket.enqueue(
        {
            "type": "session.closed",
            "session": {"id": "live-synthetic"},
            "reason": "close_requested",
        }
    )
    assert isinstance(await reader, LiveClosed)
    assert await closer is None
    assert host.closing and not host.ready and socket.closed == 1
    submit.assert_not_awaited()


@pytest.mark.asyncio
async def test_live_host_pcm_to_resident_and_barge_in_fence():
    """Exercise the actual host admission from PCM and provider events."""
    socket = FakeSocket()
    socket.enqueue(started())
    scope = VoiceScope("caller", "workspace", "", "thread")
    submit = AsyncMock(return_value="Verified result")
    now = [0.0]
    host = NativeLiveVoiceHost(
        OpenAILiveSession(socket, "gpt-live-1.1"),
        scope,
        check=AsyncMock(),
        submit=submit,
        bind_thread=AsyncMock(),
        acknowledge=AsyncMock(return_value=True),
    )
    host.turns.clock = lambda: now[0]
    await host.start(instructions="Voice conversation", voice="marin")
    await host.audio(b"\xff\x7f" * 2400)
    await host.observe(LiveTranscript("user", "Read my agenda", 100, 900, "t1"))
    await host.observe(LiveDelegation("d1", 950, "e1"))
    assert await host.poll_completed_turn() is None
    await host.audio(b"\x00\x00" * 36000)
    now[0] = 2.0
    assert await host.poll_completed_turn() == "Verified result"
    submit.assert_awaited_once_with(scope, "d1", "Read my agenda")
    await host.audio(b"\xff\x7f" * 2400)
    await host.observe(LiveTranscript("user", "List projects", 2000, 2900, "t2"))
    await host.observe(LiveDelegation("d2", 2950, "e2"))
    await host.audio(b"\x00\x00" * 36000)
    now[0] = 4.0

    async def interrupted(_request):
        await host.audio(b"\xff\x7f" * 2400)
        return True

    host.acknowledge = interrupted
    assert await host.poll_completed_turn() is None
    assert submit.await_count == 1


@pytest.mark.asyncio
async def test_live_host_uses_real_native_submission_and_preserves_pending_review(
    monkeypatch,
):
    """Follow the canonical resident entry point, rather than a fake success."""
    from app.agentive.services import work_items
    from app.agentive.work_models import WorkItem
    from app.services import chat_turn_submissions, native_voice

    scope = VoiceScope("caller", "workspace", "", "thread")
    check = AsyncMock(return_value=SimpleNamespace(id="thread"))
    monkeypatch.setattr(native_voice, "authorize", check)
    submitted = AsyncMock(
        return_value=SimpleNamespace(work_item_id="work", message_id="accepted")
    )
    monkeypatch.setattr(chat_turn_submissions, "submit_chat_turn", submitted)
    monkeypatch.setattr(
        WorkItem,
        "get",
        AsyncMock(
            return_value=SimpleNamespace(
                principal_id="caller",
                workspace_id="workspace",
                thread_id="thread",
                status="waiting_for_human",
            )
        ),
    )
    cancel = AsyncMock()
    monkeypatch.setattr(work_items, "cancel_work_item", cancel)
    socket = FakeSocket()
    socket.enqueue(started())
    host = NativeLiveVoiceHost(
        OpenAILiveSession(socket, "gpt-live-1.1"),
        scope,
        check=check,
        bind_thread=AsyncMock(),
        acknowledge=AsyncMock(return_value=True),
    )
    await host.start(instructions="Voice conversation", voice="marin")
    await host.observe(LiveDelegation("d1", 900, "e1"))
    result = await host.commit_completed_turn(
        CompletedCallerTurn("t1", 100, 1000, "Prepare the change for review")
    )
    assert "needs approval" in result
    submitted.assert_awaited_once()
    command = submitted.await_args.args[0]
    assert (command.principal_id, command.workspace_id, command.thread_id) == (
        "caller",
        "workspace",
        "thread",
    )
    assert command.execution_context.extra_data == {"voice_delegate": True}
    assert command.parts == [{"type": "text", "text": "Prepare the change for review"}]
    cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_live_browser_relay_media_admission_requires_more_than_append_ack():
    """Use the browser media framing and real Live relay, without a provider call."""
    from app.services.native_live_voice_relay import NativeLiveVoiceRelay

    class Browser:
        def __init__(self):
            self.inbox = asyncio.Queue()
            self.outbox = asyncio.Queue()

        async def receive(self):
            return await self.inbox.get()

        async def send_json(self, value):
            await self.outbox.put(value)

        async def send_bytes(self, value):
            await self.outbox.put(value)

    async def wait_for_frame(kind):
        async with asyncio.timeout(3):
            while True:
                for frame in socket.sent:
                    if frame["type"] == kind:
                        return frame
                await asyncio.sleep(0.01)

    browser = Browser()
    socket = FakeSocket()
    socket.enqueue(started())
    work = AsyncMock(return_value="Two matching records.")
    relay = NativeLiveVoiceRelay(
        browser,
        OpenAILiveSession(socket, "gpt-live-1.1"),
        VoiceScope("caller", "workspace", "", "thread"),
        check=AsyncMock(),
        work=work,
    )
    relay._watch_peer_conversation = AsyncMock(side_effect=asyncio.Event().wait)
    now = [0.0]
    relay.host.turns.clock = lambda: now[0]
    task = asyncio.create_task(relay.run())
    try:
        await wait_for_frame("session.start")
        async with asyncio.timeout(2):
            while not relay.ready:
                await asyncio.sleep(0.01)
        await browser.inbox.put(
            {"type": "websocket.receive", "bytes": b"\xff\x7f" * 2400}
        )
        await wait_for_frame("session.input_audio.append")
        socket.enqueue(
            {
                "type": "session.input_transcript.delta",
                "event_id": "user-1",
                "start_ms": 100,
                "end_ms": 900,
                "delta": "Read my agenda",
            }
        )
        socket.enqueue(
            {
                "type": "session.delegation.created",
                "event_id": "delegate-1",
                "offset_ms": 950,
                "delegation": {
                    "id": "work-1",
                    "target": "client",
                    "type": "delegation",
                },
            }
        )
        for _ in range(15):
            await browser.inbox.put(
                {"type": "websocket.receive", "bytes": b"\0\0" * 2400}
            )
        await asyncio.sleep(0.1)
        now[0] = 2.0
        silent = await wait_for_frame("session.thinking.append")
        assert silent["delegation_id"] == "work-1"
        frame = await wait_for_frame("session.commentary.append")
        assert frame["delegation_id"] == "work-1"
        assert "Read my agenda" in frame["content"]
        socket.enqueue(
            {
                "type": "session.commentary.appended",
                "client_event_id": frame["event_id"],
                "end_ms": 1000,
            }
        )
        await asyncio.sleep(0.1)
        work.assert_not_awaited()
        socket.enqueue(
            {
                "type": "session.output_transcript.delta",
                "event_id": "intent-1",
                "start_ms": 1000,
                "end_ms": 1100,
                "delta": "I'll check your agenda.",
            }
        )
        await asyncio.sleep(0.1)
        work.assert_not_awaited()
        prior_voice = relay._last_audio
        socket.enqueue(
            {
                "type": "session.output_audio.delta",
                "delta": base64.b64encode(bytes(4800)).decode(),
            }
        )
        await asyncio.sleep(0.1)
        work.assert_not_awaited()
        assert relay._last_audio == prior_voice
        pcm = b"\xff\x7f" * 2400
        socket.enqueue(
            {
                "type": "session.output_audio.delta",
                "delta": base64.b64encode(pcm).decode(),
            }
        )
        async with asyncio.timeout(3):
            while not work.await_count:
                await asyncio.sleep(0.01)
        work.assert_awaited_once_with(relay.scope, "work-1", "Read my agenda")
        async with asyncio.timeout(2):
            while not any(
                "Two matching records" in f.get("content", "") for f in socket.sent
            ):
                await asyncio.sleep(0.01)
        result_frame = next(
            f for f in socket.sent if "Two matching records" in f.get("content", "")
        )
        assert result_frame["delegation_id"] == "work-1"
        offered = []
        while not browser.outbox.empty():
            offered.append(browser.outbox.get_nowait())
        assert pcm in offered
        await browser.inbox.put(
            {"type": "websocket.receive", "text": '{"type":"microphone","muted":true}'}
        )
        await wait_for_frame("session.input_audio.mute")
        assert relay.host.microphone_muted
        await browser.inbox.put(
            {"type": "websocket.receive", "text": '{"type":"microphone","muted":false}'}
        )
        await wait_for_frame("session.input_audio.unmute")
        assert not relay.host.microphone_muted
    finally:
        await browser.inbox.put({"type": "websocket.disconnect"})
        await asyncio.wait_for(task, 3)
    assert socket.closed == 1
    assert not any(frame["type"].startswith("response.") for frame in socket.sent)


def test_live_browser_endpoint_uses_protocol_specific_media_and_keeps_scope(
    monkeypatch,
):
    """Inject only a synthetic resolved credential at the normal browser entry point."""
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from app.agentive.api import native_voice as api
    from app.services.native_live_voice_relay import NativeLiveVoiceRelay

    socket = FakeSocket()
    socket.enqueue(started())
    dial = AsyncMock(return_value=socket)
    from app.services import openai_voice_protocol

    monkeypatch.setattr(openai_voice_protocol, "connect_voice_socket", dial)
    monkeypatch.setattr(api.settings, "FRONTEND_ORIGIN", "http://127.0.0.1:9150")
    monkeypatch.setattr(api.settings, "INTEGRAL_NATIVE_DURABLE_CHAT_ENABLED", True)
    monkeypatch.setattr(
        api, "redeem_ws_ticket_shared", AsyncMock(return_value="fixture-user")
    )
    monkeypatch.setattr(
        api, "get_user_node", AsyncMock(return_value=SimpleNamespace(display_name="QA"))
    )
    check = AsyncMock(return_value=SimpleNamespace(id="fixture-thread"))
    monkeypatch.setattr(api, "authorize", check)
    monkeypatch.setattr(api, "voice_thread_continuity", lambda _: "recent")
    monkeypatch.setattr(
        api,
        "workspace_voice_profile_identity",
        AsyncMock(
            return_value=SimpleNamespace(name="QA Assistant", persona="Be concise")
        ),
    )
    monkeypatch.setattr(
        api.Workspace,
        "get",
        AsyncMock(return_value=SimpleNamespace(name="QA Workspace")),
    )
    monkeypatch.setattr(
        api,
        "resolve_realtime_credential",
        AsyncMock(
            return_value=SimpleNamespace(model="gpt-live-1.1", api_key="synthetic")
        ),
    )

    async def wait_for_peer(_self):
        await asyncio.Event().wait()

    monkeypatch.setattr(NativeLiveVoiceRelay, "_watch_peer_conversation", wait_for_peer)
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client:
        with client.websocket_connect(
            "/ws/native-voice", headers={"Origin": "http://127.0.0.1:9150"}
        ) as browser:
            browser.send_json(
                {
                    "ticket": "x" * 43,
                    "workspace_id": "fixture-workspace",
                    "thread_id": "fixture-thread",
                }
            )
            assert browser.receive_json() == {
                "type": "ready",
                "thread_id": "fixture-thread",
                "audio_protocol": "live",
            }
    dial.assert_awaited_once_with(
        "gpt-live-1.1", "synthetic", protocol=VoiceWireProtocol.LIVE
    )
    assert socket.sent[0]["type"] == "session.start"
    assert socket.closed == 1
    assert all(
        (a.args[0].principal, a.args[0].workspace, a.args[0].thread)
        == ("fixture-user", "fixture-workspace", "fixture-thread")
        for a in check.await_args_list
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer,decision",
    [
        ("Yes, go ahead", "approve"),
        ("No", "reject"),
        ("Yes, but change the date", None),
    ],
)
async def test_live_relay_review_uses_exact_capture_and_canonical_decision(
    monkeypatch, answer, decision
):
    """Live notices alone cannot decide a staged change; caller capture still can."""
    from app.agentive.services import approval_decisions
    from app.agentive.services.speech import service
    from app.services.native_live_voice_relay import NativeLiveVoiceRelay
    from app.services.native_voice_review import VoiceReviewReadout
    from tests.test_native_voice import Browser, event

    browser = Browser()
    socket = FakeSocket()
    socket.enqueue(started())
    relay = NativeLiveVoiceRelay(
        browser,
        OpenAILiveSession(socket, "gpt-live-1.1"),
        VoiceScope("caller", "workspace", "", "thread"),
        check=AsyncMock(),
        conversational_review=True,
        review_voice=SimpleNamespace(
            render=AsyncMock(return_value=b"fixture-WAV"),
            interpret_reply=AsyncMock(return_value=None),
        ),
    )
    await relay.host.start(instructions="QA", voice="marin")
    relay.ready = True
    review = VoiceReviewReadout(
        "Rename the record from Alpha to Beta.",
        "work",
        "run",
        "proposal",
        "target",
        "fingerprint",
    )
    monkeypatch.setattr(relay, "_current_review", AsyncMock(return_value=review))
    monkeypatch.setattr(
        service,
        "resolve_speech_credential",
        AsyncMock(
            return_value=SimpleNamespace(
                provider="openai", model="transcribe", source="byok", api_key="fixture"
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
    transcribe = AsyncMock(return_value=answer)
    monkeypatch.setattr(service, "transcribe_voice_confirmation", transcribe)
    commit = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(approval_decisions, "decide_staged_write", commit)
    # No provider audio or append acknowledgement constitutes review consent.
    monkeypatch.setattr(relay, "_append", AsyncMock(return_value=True))
    await relay._speak_result(
        relay.generation, result=review.text, navigation_readout=review
    )
    assert await event(browser.outbox) == {"type": "flush"}
    prompt = await event(browser.outbox)
    assert prompt["type"] == "review_confirmation"
    audio = await event(browser.outbox)
    assert audio["type"] == "review_confirmation_audio"
    assert relay.host.review_capture
    confirmation = relay.review_confirmation
    await relay.host.observe(LiveTranscript("user", "Yes", 100, 500, "provider-yes"))
    commit.assert_not_awaited()
    transcribe.assert_not_awaited()
    await relay._review_prompt_control(
        json.dumps({"type": "review_prompt_finished", "id": prompt["id"]})
    )
    confirmation.audio.extend(b"\xff\x7f" * 48000)
    confirmation.capture_done.set()
    await asyncio.wait_for(relay.confirmation_task, 2)
    assert not relay.host.review_capture
    assert relay.review_confirmation is None
    if decision:
        commit.assert_awaited_once_with(
            principal_id="caller",
            workspace_id="workspace",
            conversation_id="thread",
            thread_id="thread",
            proposal_id="proposal",
            decision=decision,
            source="voice",
        )
    else:
        commit.assert_not_awaited()
    await relay.upstream.close(timeout=0)


@pytest.mark.asyncio
async def test_shared_voice_factory_preserves_transport_scopes(monkeypatch):
    from app.services import native_voice, openai_voice_protocol
    from app.services.native_live_voice_relay import NativeLiveVoiceRelay

    wire = FakeSocket()
    dial = AsyncMock(return_value=wire)
    monkeypatch.setattr(openai_voice_protocol, "connect_voice_socket", dial)
    realtime = await native_voice.open_upstream(
        SimpleNamespace(model="gpt-realtime", api_key="synthetic")
    )
    assert realtime is wire
    live = await native_voice.open_upstream(
        SimpleNamespace(model="gpt-live-1.1", api_key="synthetic")
    )
    assert isinstance(live, OpenAILiveSession)
    scope = VoiceScope(
        "caller", "workspace", "", "thread", actions=native_voice.VoiceCallActions()
    )
    relay = native_voice.build_native_voice_relay(
        object(), live, scope, check=AsyncMock(), review_confirmation_supported=False
    )
    assert isinstance(relay, NativeLiveVoiceRelay)
    assert (
        relay.scope is scope
        and not relay.scope.actions.open_page
        and not relay.review_confirmation_supported
    )
    with pytest.raises(ValueError):
        await native_voice.open_upstream(
            SimpleNamespace(model="gpt-live-1-mini", api_key="synthetic")
        )
    assert dial.await_count == 2


@pytest.mark.asyncio
async def test_public_live_media_rechecks_grant_and_cannot_delegate(monkeypatch):
    from app.services.native_live_public_voice import (
        NativeLivePublicVoice,
        build_public_voice_relay,
    )
    from tests.test_native_voice import Browser, event

    browser = Browser()
    wire = FakeSocket()
    wire.enqueue(started())
    allowed = True

    async def grant():
        return allowed

    session = OpenAILiveSession(wire, "gpt-live-1.1")
    relay = build_public_voice_relay(
        browser, session, "A public greeting.", grant, realtime_factory=lambda *_: None
    )
    assert isinstance(relay, NativeLivePublicVoice)
    task = asyncio.create_task(relay.run())
    try:
        assert await event(browser.outbox) == {"type": "ready"}
        await browser.inbox.put({"type": "websocket.receive", "bytes": b"\x00\x01" * 8})
        wire.enqueue(
            {
                "type": "session.delegation.created",
                "event_id": "public-delegation",
                "offset_ms": 100,
                "delegation": {"id": "d", "type": "delegation", "target": "client"},
            }
        )
        wire.enqueue(
            {
                "type": "session.output_audio.delta",
                "delta": base64.b64encode(b"\x02\x00").decode(),
            }
        )
        assert await event(browser.outbox) == b"\x02\x00"
        async with asyncio.timeout(2):
            while not any(f["type"] == "session.thinking.append" for f in wire.sent):
                await asyncio.sleep(0.01)
        assert not any("tools" in f.get("session", {}) for f in wire.sent)
        assert "No backend data or action" in next(
            f["content"] for f in wire.sent if f["type"] == "session.thinking.append"
        )
        allowed = False
        wire.enqueue(
            {
                "type": "session.output_audio.delta",
                "delta": base64.b64encode(b"\x04\x00").decode(),
            }
        )
        await asyncio.wait_for(task, 3)
        assert browser.outbox.empty()
        assert wire.closed == 1
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_live_defers_quiet_context_without_input_clock_during_review():
    """A muted or unclocked Live session cannot accept fresh context injection."""
    from app.services.native_live_voice_relay import NativeLiveVoiceRelay
    from tests.test_native_voice import Browser

    socket = FakeSocket()
    socket.enqueue(started())
    relay = NativeLiveVoiceRelay(
        Browser(),
        OpenAILiveSession(socket, "gpt-live-1.1"),
        VoiceScope("caller", "workspace", "", "thread"),
        check=AsyncMock(),
    )
    await relay.host.start(instructions="QA", voice="marin")
    relay.ready = True
    relay._append = AsyncMock(return_value=True)

    def status(text):
        return {
            "type": "conversation.item.create",
            "event_id": "voice-status-fixture",
            "item": {"role": "system", "content": [{"text": text}]},
        }

    assert not relay._can_sync_thread_status()
    await relay.send_event(status("initial snapshot"))
    relay._append.assert_not_awaited()
    await relay.host.set_review_capture(True)
    await relay.send_event(status("newer verified snapshot"))
    assert not relay._can_sync_thread_status()
    relay._append.assert_not_awaited()
    await relay.host.set_review_capture(False)
    await relay.send_event(
        {
            "type": "input_audio_buffer.append",
            "audio": base64.b64encode(bytes(4800)).decode(),
        }
    )
    await asyncio.wait_for(relay._system_flush_task, 2)
    relay._append.assert_awaited_once_with("newer verified snapshot")
    assert not relay._deferred_system
    await relay.upstream.close(timeout=0)


@pytest.mark.asyncio
async def test_live_pending_followup_without_receipt_does_not_promise_execution():
    from app.services.native_live_voice_relay import NativeLiveVoiceRelay
    from app.services.openai_voice_protocol import LiveTaskRequest
    from tests.test_native_voice import Browser

    socket = FakeSocket()
    relay = NativeLiveVoiceRelay(
        Browser(),
        OpenAILiveSession(socket, "gpt-live-1.1"),
        VoiceScope("caller", "workspace", "", "thread"),
        check=AsyncMock(),
    )
    relay._append = AsyncMock(return_value=True)
    relay.pending = asyncio.create_task(asyncio.Event().wait())
    try:
        turn = CompletedCallerTurn("turn", 0, 1500, "After that, summarize my calendar")
        assert not await relay._acknowledge(LiveTaskRequest("delegation", turn))
        relay._append.assert_awaited_once()
        assert "Nothing has been submitted" in relay._append.call_args.args[0]
    finally:
        relay.pending.cancel()
        await asyncio.gather(relay.pending, return_exceptions=True)


@pytest.mark.parametrize(
    ("utterance", "operation", "content"),
    [
        ("After that, summarize my calendar", "queue", "summarize my calendar"),
        ("Queue a separate follow-up: show the deposit", "queue", "show the deposit"),
        ("Correction to the current task: use June", "steer", "use June"),
        ("Correct the running request, use June", "steer", "use June"),
        (
            "Correction to the current task. Also include Cedar",
            "steer",
            "Also include Cedar",
        ),
        ("After that. List apps", "queue", "List apps"),
    ],
)
@pytest.mark.asyncio
async def test_live_explicit_continuation_pins_receipt_and_uses_shared_route(
    utterance, operation, content
):
    from app.services.native_live_voice_relay import NativeLiveVoiceRelay
    from app.services.native_voice import _AcceptedVoiceTurn
    from app.services.openai_voice_protocol import LiveTaskRequest
    from tests.test_native_voice import Browser

    relay = NativeLiveVoiceRelay(
        Browser(),
        OpenAILiveSession(FakeSocket(), "gpt-live-1"),
        VoiceScope("caller", "workspace", "", "thread"),
        check=AsyncMock(),
    )
    relay._append = AsyncMock(return_value=True)
    relay._follow_up = AsyncMock()
    relay.pending = asyncio.create_task(asyncio.Event().wait())
    relay.pending_accepted = True
    receipt = _AcceptedVoiceTurn("active-work", "message", "initial request")
    relay.accepted = receipt
    relay.steerable_work_id = receipt.work_item_id
    try:
        turn = CompletedCallerTurn("turn", 0, 1500, utterance)
        assert await relay._acknowledge(LiveTaskRequest("delegation", turn))
        await relay._submit(relay.scope, "delegation", utterance)
        await relay.follow_up_task
        relay._follow_up.assert_awaited_once_with(
            "delegation", content, operation, relay.generation, receipt
        )
    finally:
        relay.pending.cancel()
        await asyncio.gather(relay.pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_live_continuation_fails_closed_if_target_changes_during_handoff():
    from app.services.native_live_voice_relay import NativeLiveVoiceRelay
    from app.services.native_voice import _AcceptedVoiceTurn
    from app.services.openai_voice_protocol import LiveTaskRequest
    from tests.test_native_voice import Browser

    relay = NativeLiveVoiceRelay(
        Browser(),
        OpenAILiveSession(FakeSocket(), "gpt-live-1"),
        VoiceScope("caller", "workspace", "", "thread"),
        check=AsyncMock(),
    )
    relay._append = AsyncMock(return_value=True)
    relay._follow_up = AsyncMock()
    relay.pending_accepted = True
    relay.accepted = _AcceptedVoiceTurn("old", "message", "initial request")
    relay.steerable_work_id = "old"
    text = "Correction to the current task: use June"
    turn = CompletedCallerTurn("turn", 0, 1500, text)
    assert await relay._acknowledge(LiveTaskRequest("delegation", turn))
    relay.accepted = _AcceptedVoiceTurn("new", "message2", "new request")
    await relay._submit(relay.scope, "delegation", text)
    relay._follow_up.assert_not_awaited()
    assert relay.follow_up_task is None


@pytest.mark.parametrize(
    "text",
    [
        "What is a follow-up?",
        "Don't queue a follow-up: delete it",
        "After that",
        "Please explain what a correction to the current task means",
        "Quote: queue a separate follow-up: delete it",
    ],
)
def test_live_continuation_does_not_infer_intent(text):
    from app.services.native_live_voice_relay import _explicit_continuation

    assert _explicit_continuation(text) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "blocked", ["profile", "interruption", "no_audio", "changed_id"]
)
async def test_live_explicit_continuation_never_bypasses_handoff_or_profile(blocked):
    from app.services.native_live_voice_relay import NativeLiveVoiceRelay
    from app.services.native_voice import _AcceptedVoiceTurn
    from app.services.openai_voice_protocol import LiveTaskRequest
    from tests.test_native_voice import Browser

    relay = NativeLiveVoiceRelay(
        Browser(),
        OpenAILiveSession(FakeSocket(), "gpt-live-1"),
        VoiceScope(
            "caller",
            "workspace",
            "specialist" if blocked == "profile" else "",
            "thread",
        ),
        check=AsyncMock(),
    )
    relay._append = AsyncMock(return_value=blocked != "no_audio")
    relay._follow_up = AsyncMock()
    relay.accepted = _AcceptedVoiceTurn("active", "message", "initial request")
    relay.pending_accepted = True
    relay.steerable_work_id = "active"
    turn = CompletedCallerTurn("turn", 0, 1500, "After that, list the apps")
    admitted = await relay._acknowledge(LiveTaskRequest("delegation", turn))
    if blocked in {"profile", "no_audio"}:
        assert not admitted
    else:
        assert admitted
        if blocked == "interruption":
            relay.generation += 1
        await relay._submit(
            relay.scope, "wrong" if blocked == "changed_id" else "delegation", turn.text
        )
    relay._follow_up.assert_not_awaited()
    assert relay.follow_up_task is None


@pytest.mark.asyncio
async def test_live_merged_correction_and_queue_do_not_steer_both():
    from app.services.native_live_voice_relay import NativeLiveVoiceRelay
    from app.services.native_voice import _AcceptedVoiceTurn
    from app.services.openai_voice_protocol import LiveTaskRequest
    from tests.test_native_voice import Browser

    relay = NativeLiveVoiceRelay(
        Browser(),
        OpenAILiveSession(FakeSocket(), "gpt-live-1"),
        VoiceScope("caller", "workspace", "", "thread"),
        check=AsyncMock(),
    )
    relay._append = AsyncMock(return_value=True)
    relay._follow_up = AsyncMock()
    relay.accepted = _AcceptedVoiceTurn("active", "message", "initial request")
    relay.pending_accepted = True
    relay.steerable_work_id = "active"
    text = "Correction to the current task: Also include cedar After that, list the apps in this workspace"
    turn = CompletedCallerTurn("turn", 0, 1500, text)
    assert not await relay._acknowledge(LiveTaskRequest("delegation", turn))
    assert "Neither operation has been submitted" in relay._append.call_args.args[0]
    relay._follow_up.assert_not_awaited()


def test_live_ledger_accepts_local_explicit_turn_once_without_forging_provider_event():
    ledger = LiveTurnLedger()
    turn = CompletedCallerTurn(
        "turn", 10, 1500, "Correction to the current task. Use June"
    )
    assert ledger.commit(turn, accept_explicit_continuation=True) == LiveTaskRequest(
        "local-continuation-turn", turn, provider_delegated=False
    )
    assert ledger.commit(turn, accept_explicit_continuation=True) is None


@pytest.mark.asyncio
async def test_live_host_routes_explicit_completed_turn_without_provider_delegation():
    from app.services.native_live_voice_host import NativeLiveVoiceHost

    scope = VoiceScope("caller", "workspace", "", "thread")
    session = OpenAILiveSession(FakeSocket(), "gpt-live-1")
    acknowledge = AsyncMock(return_value=True)
    submit = AsyncMock(return_value="accepted")
    host = NativeLiveVoiceHost(
        session,
        scope,
        check=AsyncMock(),
        acknowledge=acknowledge,
        submit=submit,
        accept_explicit_continuations=True,
    )
    host.ready = True
    text = "Correction to the current task. Also include Cedar"
    turn = CompletedCallerTurn("local", 10, 1500, text)
    assert await host.commit_completed_turn(turn) == "accepted"
    request = acknowledge.call_args.args[0]
    assert not request.provider_delegated
    submit.assert_awaited_once_with(scope, "local-continuation-local", text)
    assert await host.commit_completed_turn(turn) is None
    assert (
        await host.commit_completed_turn(
            CompletedCallerTurn("ordinary", 1600, 2000, "Hello")
        )
        is None
    )
    assert submit.await_count == 1


@pytest.mark.asyncio
async def test_live_host_explicit_end_uses_relay_terminal_path_without_delegation():
    scope = VoiceScope("caller", "workspace", "", "thread")
    session = OpenAILiveSession(FakeSocket(), "gpt-live-1")
    end = AsyncMock()
    submit = AsyncMock()
    host = NativeLiveVoiceHost(
        session,
        scope,
        check=AsyncMock(),
        acknowledge=AsyncMock(),
        submit=submit,
        request_end=end,
    )
    host.ready = True
    await host.commit_completed_turn(
        CompletedCallerTurn("end", 0, 1500, "Okay, please end this call now.")
    )
    end.assert_awaited_once()
    submit.assert_not_awaited()
    assert not session.closed


@pytest.mark.asyncio
async def test_live_relay_notifies_browser_end_even_without_provider_finalization():
    from app.services.native_live_voice_relay import NativeLiveVoiceRelay
    from tests.test_native_voice import Browser

    browser = Browser()
    session = OpenAILiveSession(FakeSocket(), "gpt-live-1")
    session.close = AsyncMock(return_value=False)
    relay = NativeLiveVoiceRelay(
        browser,
        session,
        VoiceScope("caller", "workspace", "", "thread"),
        check=AsyncMock(),
    )
    relay.host.ready = True
    await relay._request_live_end()
    await relay._watch_live_end()
    assert await browser.outbox.get() == {"type": "call_ending"}
    assert await browser.outbox.get() == {"type": "call_ended"}
    assert relay.host.closing
    assert not relay.host.ready


@pytest.mark.asyncio
async def test_live_settings_advertise_the_same_exact_registry_as_backend_validation(
    monkeypatch,
):
    from app.services import voice_configuration as config

    monkeypatch.setattr(config, "can_access_workspace", AsyncMock(return_value="owner"))
    monkeypatch.setattr(config, "owner_subject", AsyncMock(return_value="caller"))
    monkeypatch.setattr(config, "configuration_record", AsyncMock(return_value=None))
    monkeypatch.setattr(
        config, "resolve_realtime_credential", AsyncMock(return_value=None)
    )
    response = await config.describe_realtime("caller", "workspace")
    assert set(response["supported_models"]) == REALTIME_MODELS | LIVE_MODELS
    assert "gpt-live-1-mini" not in response["supported_models"]


def test_live_turn_boundary_keeps_greeting_separate_from_explicit_hangup():
    now = [0.0]
    turns = LiveCallerTurns(clock=lambda: now[0])
    turns.audio(b"\xff\x7f" * 2400)
    turns.observe(LiveTranscript("user", "Hello, say hello back", 100, 900, "greeting"))
    now[0] = 1.9
    turns.audio(bytes(72000))
    assert turns.poll() is None  # No delegation yet; normal 3s wait.
    # New speech after the strong PCM boundary settles the earlier social turn.
    turns.audio(b"\xff\x7f" * 2400)
    turns.observe(
        LiveTranscript("user", "Okay, please end this call now", 2200, 3000, "hangup")
    )
    previous = turns.poll()
    assert previous and previous.text == "Hello, say hello back" and previous.superseded
    turns.audio(bytes(72000))
    now[0] = 5.1
    ending = turns.poll()
    assert (
        ending
        and ending.text == "Okay, please end this call now"
        and not ending.superseded
    )


@pytest.mark.asyncio
async def test_live_superseded_end_cannot_close_call_or_admit_late_delegation():
    scope = VoiceScope("caller", "workspace", "", "thread")
    end = AsyncMock()
    submit = AsyncMock()
    host = NativeLiveVoiceHost(
        OpenAILiveSession(FakeSocket(), "gpt-live-1"),
        scope,
        check=AsyncMock(),
        acknowledge=AsyncMock(return_value=True),
        submit=submit,
        request_end=end,
        accept_explicit_continuations=True,
    )
    host.ready = True
    host.ledger.observe(LiveDelegation("late", 900, "late-event"))
    assert (
        await host.commit_completed_turn(
            CompletedCallerTurn(
                "superseded", 100, 1200, "Please end this call", superseded=True
            )
        )
        is None
    )
    end.assert_not_awaited()
    submit.assert_not_awaited()


def test_live_playback_speech_gate_debounces_noise_and_releases_without_waiting_for_command_commit():
    from app.services.native_live_voice_relay import _LiveBargeIn

    gate = _LiveBargeIn()
    voice = b"\xff\x7f" * 2400  # 100 ms
    quiet = bytes(4800)
    assert not gate.update(voice)  # a click or isolated noise cannot flush
    assert not gate.update(quiet)
    assert not gate.update(voice)
    assert gate.update(voice)  # sustained caller speech wins
    assert gate.update(quiet)
    assert not gate.update(quiet)  # 200 ms quiet, not 1.5 s admission fence


@pytest.mark.asyncio
async def test_live_media_releases_first_reply_words_while_command_turn_still_settling():
    from app.services.native_live_voice_relay import NativeLiveVoiceRelay
    from tests.test_native_voice import Browser

    browser = Browser()
    session = OpenAILiveSession(FakeSocket(), "gpt-live-1")
    relay = NativeLiveVoiceRelay(
        browser,
        session,
        VoiceScope("caller", "workspace", "", "thread"),
        check=AsyncMock(),
    )
    relay.host.ready = True
    relay.host.session.append_audio = AsyncMock()
    data = b"\xff\x7f" * 2400

    async def send(pcm):
        await relay.send_event(
            {
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(pcm).decode(),
            }
        )

    await send(data)
    assert browser.outbox.empty()
    await send(data)
    assert relay.speaking
    assert await browser.outbox.get() == {"type": "flush"}
    await send(bytes(4800))
    assert relay.speaking
    await send(bytes(4800))
    assert not relay.speaking
    assert relay.host.turns.speaking  # independent, conservative command fence
    assert relay.protocol_counts["live_caller_barge_in"] == 1
    assert relay.protocol_counts["live_caller_playback_released"] == 1


@pytest.mark.asyncio
async def test_live_opening_uses_authenticated_name_naturally():
    from app.services.native_live_voice_relay import NativeLiveVoiceRelay
    from tests.test_native_voice import Browser

    relay = NativeLiveVoiceRelay(
        Browser(),
        OpenAILiveSession(FakeSocket(), "gpt-live-1"),
        VoiceScope("caller", "workspace", "", "thread"),
        check=AsyncMock(),
    )
    relay.caller_name = "Eldon Marks"
    relay._append = AsyncMock(return_value=True)
    await relay._send_opening()
    assert "Eldon Marks" in relay._append.call_args.args[0]
    assert "known name" in relay._append.call_args.args[0]
    assert "never repeat it in every reply" in relay._append.call_args.args[0]


@pytest.mark.asyncio
async def test_live_commentary_waits_for_prior_output_to_finish_instead_of_cutting_it():
    from app.services.native_live_voice_relay import NativeLiveVoiceRelay
    from tests.test_native_voice import Browser

    session = OpenAILiveSession(FakeSocket(), "gpt-live-1")
    relay = NativeLiveVoiceRelay(
        Browser(),
        session,
        VoiceScope("caller", "workspace", "", "thread"),
        check=AsyncMock(),
    )
    relay.host.ready = True
    relay._input_clock.set()
    session.append_context = AsyncMock()
    relay._last_audio = asyncio.get_running_loop().time()
    task = asyncio.create_task(relay._append("Verified answer", spoken=True))
    try:
        await asyncio.sleep(0.1)
        session.append_context.assert_not_awaited()
        # The output falls quiet; a new commentary can now begin.
        relay._last_audio = asyncio.get_running_loop().time() - 1
        async with asyncio.timeout(1):
            while not session.append_context.await_count:
                await asyncio.sleep(0.01)
        relay._commentary.offset = 1
        relay._commentary.transcript = relay._commentary.audio = True
        relay._commentary.changed.set()
        assert await task
        session.append_context.assert_awaited_once()
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_live_audio_frame_is_discarded_if_call_closes_during_authorization():
    session = OpenAILiveSession(FakeSocket(), "gpt-live-1")
    scope = VoiceScope("caller", "workspace", "", "thread")
    host = NativeLiveVoiceHost(
        session,
        scope,
        check=AsyncMock(),
        acknowledge=AsyncMock(),
    )
    host.ready = True

    async def closing_check(_scope):
        session.closing = True
        host.ready = False

    host.check = closing_check
    session.append_audio = AsyncMock()
    await host.audio(bytes(4800))
    session.append_audio.assert_not_awaited()
