"""Microphone and playback interruptions on a live call."""

import asyncio
import base64
import json

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


@pytest.mark.parametrize("done_before_interrupt", [True, False])
async def test_interruption_truncates_only_unplayed_audio_of_the_bound_item(
    done_before_interrupt,
):
    """Provider generation can finish while the browser still has queued audio."""
    browser, provider = Browser(), Provider()
    relay = VoiceRelay(
        browser,
        provider,
        VoiceScope("caller", "workspace", "", "thread"),
        check=authorize,
    )
    call = asyncio.create_task(relay.run())

    async def report(items):
        await browser.inbox.put(
            {
                "type": "websocket.receive",
                "text": json.dumps({"type": "playback_interrupted", "items": items}),
            }
        )

    try:
        await event(provider.outbox)
        await provider.inbox.put({"type": "session.updated"})
        await event(browser.outbox)
        await provider.inbox.put({"type": "response.created", "response": {"id": "r1"}})
        pcm = bytes(24000)
        for index in range(2):
            await provider.inbox.put(
                {
                    "type": "response.output_audio.delta",
                    "response_id": "r1",
                    "item_id": "provider-item",
                    "content_index": 0,
                    "delta": base64.b64encode(pcm).decode(),
                }
            )
            if index == 0:
                marker = await event(browser.outbox)
                assert marker["type"] == "playback_item"
                handle = marker["handle"]
            assert await event(browser.outbox) == pcm
        if done_before_interrupt:
            await provider.inbox.put(
                {
                    "type": "response.done",
                    "response": {"id": "r1", "status": "completed"},
                }
            )
        await provider.inbox.put({"type": "input_audio_buffer.speech_started"})
        assert await event(browser.outbox) == {"type": "flush"}
        # Unknown, out-of-range, and repeated claims cannot change provider history.
        await report(
            [[handle + 1, 100], [handle, 24001], [handle, 12000], [handle, 42]]
        )
        assert await event(provider.outbox) == {
            "type": "conversation.item.truncate",
            "item_id": "provider-item",
            "content_index": 0,
            "audio_end_ms": 500,
        }
        await browser.inbox.put({"type": "websocket.receive", "bytes": b"\x00\x01"})
        assert (await event(provider.outbox))["type"] == "input_audio_buffer.append"
    finally:
        await browser.inbox.put({"type": "websocket.disconnect"})
        await call
