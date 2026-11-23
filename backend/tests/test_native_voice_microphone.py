"""Mute affects capture, never output or already accepted work."""

import asyncio

import pytest

from app.services.native_voice import VoiceRelay, VoiceScope
from tests.test_native_voice import Browser, Provider, authorize, event

pytestmark = [pytest.mark.asyncio, pytest.mark.smoke]


async def test_mute_drops_input_and_clears_unfinished_vad_without_ending_playback():
    browser, provider = Browser(), Provider()
    relay = VoiceRelay(
        browser,
        provider,
        VoiceScope("caller", "workspace", "", "thread"),
        check=authorize,
    )
    task = asyncio.create_task(relay.run())
    try:
        assert (await event(provider.outbox))["type"] == "session.update"
        await provider.inbox.put({"type": "session.updated"})
        assert (await event(browser.outbox))["type"] == "ready"

        await provider.inbox.put({"type": "input_audio_buffer.speech_started"})
        assert await event(browser.outbox) == {"type": "flush"}
        assert relay.speaking
        await browser.inbox.put(
            {"type": "websocket.receive", "text": '{"type":"microphone","muted":true}'}
        )
        assert await event(provider.outbox) == {"type": "input_audio_buffer.clear"}
        assert relay.microphone_muted and not relay.speaking
        await browser.inbox.put({"type": "websocket.receive", "bytes": b"\x01\x00"})
        await browser.inbox.put(
            {"type": "websocket.receive", "text": '{"type":"microphone","muted":true}'}
        )
        await provider.inbox.put({"type": "input_audio_buffer.speech_started"})
        await provider.inbox.put(
            {"type": "response.created", "response": {"id": "agent"}}
        )
        await provider.inbox.put(
            {
                "type": "response.output_audio.delta",
                "response_id": "agent",
                "delta": "AAE=",
            }
        )
        assert await event(browser.outbox) == b"\x00\x01"
        assert provider.outbox.empty()
        assert not relay.speaking

        await browser.inbox.put(
            {"type": "websocket.receive", "text": '{"type":"microphone","muted":false}'}
        )
        await browser.inbox.put({"type": "websocket.receive", "bytes": b"\x00\x02"})
        assert await event(provider.outbox) == {
            "type": "input_audio_buffer.append",
            "audio": "AAI=",
        }
        assert not relay.microphone_muted
    finally:
        await browser.inbox.put({"type": "websocket.disconnect"})
        await asyncio.wait_for(task, 3)
