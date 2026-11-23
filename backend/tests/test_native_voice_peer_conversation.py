"""Voice receives bounded committed Text peer updates during the same call."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import native_voice
from tests.test_native_voice import Browser, Provider, event
from tests.test_native_voice_completion import _send_tool


class _Subscription:
    def __init__(self):
        self.dirty = asyncio.Event()
        self.closed = False

    async def wait(self):
        await self.dirty.wait()
        self.dirty.clear()

    def close(self):
        self.closed = True


def _row(message_id, role, text, *, source="typed", kind="message", count=0):
    return SimpleNamespace(
        message_id=message_id,
        role=role,
        text=text,
        source=source,
        kind=kind,
        attachment_count=count,
    )


async def _audio(provider, browser, name, frame):
    await provider.inbox.put(
        {
            "type": "response.created",
            "response": {"id": name, "metadata": frame["response"]["metadata"]},
        }
    )
    await provider.inbox.put(
        {"type": "response.output_audio.delta", "response_id": name, "delta": "AAE="}
    )
    assert await event(browser.outbox) == b"\x00\x01"
    await provider.inbox.put(
        {"type": "response.done", "response": {"id": name, "status": "completed"}}
    )


@pytest.mark.asyncio
async def test_same_thread_typed_events_wake_and_speak_once_at_pause(monkeypatch):
    """Initial history, duplicate, foreign provenance and raw attachment data stay out."""
    browser, provider, sub = Browser(), Provider(), _Subscription()
    scoped = native_voice.VoiceScope("caller", "workspace", "profile", "chat")
    bound = []
    read_cursors = []
    baseline_done = asyncio.Event()
    snapshot = SimpleNamespace(
        cursor="baseline",
        gap=False,
        updates=(_row("historical", "assistant", "Do not replay history"),),
    )

    def subscribe(*key: str):
        bound.append(key)
        return sub

    async def read(*key: str, profile_key, cursor):
        assert key == ("caller", "workspace", "chat") and profile_key == "profile"
        read_cursors.append(cursor)
        baseline_done.set()
        return snapshot

    heavy = AsyncMock()
    started = asyncio.Event()

    async def held_heavy(_scope, _call_id, _text):
        started.set()
        return await asyncio.Future()

    heavy.side_effect = held_heavy
    monkeypatch.setattr(native_voice, "RESULT_IDLE_SECONDS", 0.01)
    monkeypatch.setattr(native_voice, "RESULT_POLL_SECONDS", 0.01)
    relay = native_voice.VoiceRelay(
        browser,
        provider,
        scoped,
        work=heavy,
        check=AsyncMock(return_value=SimpleNamespace(id="chat")),
        conversation_subscribe=subscribe,
        conversation_snapshot=read,
    )
    task = asyncio.create_task(relay.run())
    try:
        await event(provider.outbox)
        await provider.inbox.put({"type": "session.updated"})
        assert (await event(browser.outbox))["thread_id"] == "chat"
        await baseline_done.wait()
        assert not relay.peer_readouts
        await _send_tool(provider, "heavy", "heavy")
        await asyncio.wait_for(started.wait(), 1)
        await provider.inbox.put({"type": "input_audio_buffer.speech_started"})
        assert await event(browser.outbox) == {"type": "flush"}

        snapshot = SimpleNamespace(
            cursor="write",
            gap=False,
            updates=(
                _row(
                    "typed", "user", "Private prompt must not be proactively repeated"
                ),
                _row("file", "user", "raw file reference", kind="attachments", count=1),
                _row(
                    "voice-result",
                    "assistant",
                    "Do not duplicate voice result",
                    source="voice",
                ),
                _row(
                    "unknown",
                    "assistant",
                    "Do not announce unknown origin",
                    source="unknown",
                ),
                _row("typed-result", "assistant", "The chat task is complete."),
            ),
        )
        sub.dirty.set()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(provider.outbox.get(), 0.06)
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

        for name, text in (
            ("accepted", "I see your typed message"),
            ("attachment", "A file is attached"),
            ("result", "The chat task is complete."),
        ):
            readout = await event(provider.outbox)
            prompt = readout["response"]["instructions"]
            assert text in prompt and "raw file reference" not in prompt
            assert "Private prompt" not in prompt and "tool_choice" not in prompt
            assert readout["response"]["tool_choice"] == "none"
            await _audio(provider, browser, name, readout)
        sub.dirty.set()  # duplicate wake/projection
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(provider.outbox.get(), 0.1)
        assert relay.protocol_counts["peer_readout_sent"] == 3
        assert read_cursors == [None, "baseline", "write"]
        assert bound == [("caller", "workspace", "chat")]
        heavy.assert_awaited_once()
        assert not relay.pending.done()
    finally:
        await browser.inbox.put({"type": "websocket.disconnect"})
        await task
    assert sub.closed


@pytest.mark.asyncio
async def test_peer_gap_advances_cursor_without_readout(monkeypatch):
    """A bounded history gap is not permission to narrate old text on reconnect."""
    browser, provider, sub = Browser(), Provider(), _Subscription()
    started = asyncio.Event()
    calls = []

    async def read(*key: str, profile_key, cursor):
        calls.append(cursor)
        started.set()
        return (
            SimpleNamespace(cursor="initial", gap=False, updates=())
            if cursor is None
            else SimpleNamespace(
                cursor="after-gap",
                gap=True,
                updates=(_row("old", "assistant", "Do not replay"),),
            )
        )

    monkeypatch.setattr(native_voice, "RESULT_POLL_SECONDS", 0.01)
    relay = native_voice.VoiceRelay(
        browser,
        provider,
        native_voice.VoiceScope("caller", "workspace", "", "chat"),
        check=AsyncMock(return_value=SimpleNamespace(id="chat")),
        conversation_subscribe=lambda *_key: sub,
        conversation_snapshot=read,
    )
    task = asyncio.create_task(relay.run())
    try:
        await event(provider.outbox)
        await provider.inbox.put({"type": "session.updated"})
        await event(browser.outbox)
        await started.wait()
        sub.dirty.set()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(provider.outbox.get(), 0.10)
        assert calls == [None, "initial"]
        assert relay.peer_readouts == {}
    finally:
        await browser.inbox.put({"type": "websocket.disconnect"})
        await task


@pytest.mark.asyncio
async def test_peer_readout_ack_requires_browser_audio_and_retries_once(monkeypatch):
    """An unplayed provider response does not acknowledge a typed reply."""
    browser, provider, sub = Browser(), Provider(), _Subscription()
    started = asyncio.Event()

    async def read(*key: str, profile_key, cursor):
        started.set()
        return (
            SimpleNamespace(cursor="initial", gap=False, updates=())
            if cursor is None
            else SimpleNamespace(
                cursor="next",
                gap=False,
                updates=(_row("answer", "assistant", "Verified typed reply."),),
            )
        )

    monkeypatch.setattr(native_voice, "RESULT_POLL_SECONDS", 0.01)
    monkeypatch.setattr(native_voice, "RESULT_IDLE_SECONDS", 0.01)
    relay = native_voice.VoiceRelay(
        browser,
        provider,
        native_voice.VoiceScope("caller", "workspace", "", "chat"),
        check=AsyncMock(return_value=SimpleNamespace(id="chat")),
        conversation_subscribe=lambda *_key: sub,
        conversation_snapshot=read,
    )
    task = asyncio.create_task(relay.run())
    try:
        await event(provider.outbox)
        await provider.inbox.put({"type": "session.updated"})
        await event(browser.outbox)
        await started.wait()
        sub.dirty.set()
        initial = await event(provider.outbox)
        await provider.inbox.put(
            {
                "type": "response.created",
                "response": {
                    "id": "muted",
                    "metadata": initial["response"]["metadata"],
                },
            }
        )
        await provider.inbox.put(
            {
                "type": "response.done",
                "response": {"id": "muted", "status": "completed"},
            }
        )
        retry = await event(provider.outbox)
        assert retry["event_id"] != initial["event_id"]
        assert "Verified typed reply." in retry["response"]["instructions"]
        assert relay.protocol_counts["peer_readout_sent"] == 0
        assert relay.peer_readouts == {
            ("answer", "message"): "The chat reply says: Verified typed reply."
        }
        await _audio(provider, browser, "retried", retry)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(provider.outbox.get(), 0.06)
        assert relay.peer_readouts == {}
        assert relay.protocol_counts["peer_readout_sent"] == 1
    finally:
        await browser.inbox.put({"type": "websocket.disconnect"})
        await task


@pytest.mark.asyncio
async def test_committed_chat_wake_projects_through_real_scope_and_voice_relay(
    monkeypatch,
):
    """The shared harness projection feeds actual same-thread voice routing."""
    from app.services import chat_conversation_updates, chat_threads
    from app.services.chat_thread_event_delivery import queue_committed_chat_event
    from app.services.chat_turn_transcript import _message_id
    from tests.test_chat_conversation_updates import _event, _thread_fixture, _work

    owner, outsider, workspace, thread = await _thread_fixture()
    real_read = chat_conversation_updates.read_conversation_snapshot
    initialized = asyncio.Event()

    async def read(*key: str, profile_key, cursor):
        snapshot = await real_read(*key, profile_key=profile_key, cursor=cursor)
        initialized.set()
        return snapshot

    monkeypatch.setattr(chat_conversation_updates, "read_conversation_snapshot", read)
    monkeypatch.setattr(native_voice, "RESULT_POLL_SECONDS", 0.01)
    monkeypatch.setattr(native_voice, "RESULT_IDLE_SECONDS", 0.01)
    browser, provider = Browser(), Provider()
    relay = native_voice.VoiceRelay(
        browser,
        provider,
        native_voice.VoiceScope(owner.id, workspace.id, "", thread.id),
    )
    task = asyncio.create_task(relay.run())
    try:
        await event(provider.outbox)
        await provider.inbox.put({"type": "session.updated"})
        assert (await event(browser.outbox))["thread_id"] == thread.id
        await asyncio.wait_for(initialized.wait(), 2)

        note = await chat_threads.append_message(
            thread=thread,
            role="user",
            parts=[{"type": "text", "text": "Private typed prompt"}],
        )
        await _work(thread, note, "peer-proof", typed=True)
        response = await chat_threads.append_message(
            thread=thread,
            role="assistant",
            message_id=_message_id("peer-proof"),
            parent_id=note.id,
            parts=[{"type": "text", "text": "The typed peer answer is ready."}],
        )
        wake = _event(thread, response)
        from dataclasses import replace

        queue_committed_chat_event(replace(wake, scope=f"user:{outsider.id}"))
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(provider.outbox.get(), 0.05)
        queue_committed_chat_event(wake)
        acceptance = await event(provider.outbox)
        assert "I see your typed message" in acceptance["response"]["instructions"]
        assert "Private typed prompt" not in acceptance["response"]["instructions"]
        await _audio(provider, browser, "acceptance", acceptance)
        reply = await event(provider.outbox)
        assert "The typed peer answer is ready." in reply["response"]["instructions"]
        await _audio(provider, browser, "reply", reply)
        assert relay.accepted is None
    finally:
        await browser.inbox.put({"type": "websocket.disconnect"})
        await task


@pytest.mark.asyncio
async def test_voice_ready_waits_for_subscription_and_no_replay_baseline():
    """A commit immediately after visible Ready cannot be swallowed as history."""
    browser, provider, sub = Browser(), Provider(), _Subscription()
    started, release = asyncio.Event(), asyncio.Event()

    async def read(*key: str, profile_key, cursor):
        assert cursor is None
        started.set()
        await release.wait()
        return SimpleNamespace(cursor="baseline", gap=False, updates=())

    relay = native_voice.VoiceRelay(
        browser,
        provider,
        native_voice.VoiceScope("caller", "workspace", "", "chat"),
        check=AsyncMock(return_value=SimpleNamespace(id="chat")),
        conversation_subscribe=lambda *_key: sub,
        conversation_snapshot=read,
    )
    task = asyncio.create_task(relay.run())
    try:
        await event(provider.outbox)
        await provider.inbox.put({"type": "session.updated"})
        await asyncio.wait_for(started.wait(), 1)
        assert browser.outbox.empty()
        release.set()
        assert (await event(browser.outbox))["thread_id"] == "chat"
        assert relay.conversation_baselined.is_set()
    finally:
        release.set()
        await browser.inbox.put({"type": "websocket.disconnect"})
        await task
