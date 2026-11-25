"""Quiet status reaches only the current relay without starting any response."""

from unittest.mock import AsyncMock

import pytest

from app.api.errors import InsufficientPermissionsError
from app.services import native_voice
from app.services.native_voice_thread_status import VoiceThreadStatus
from tests.test_native_voice import Browser, Provider

pytestmark = [pytest.mark.asyncio, pytest.mark.smoke]


def relay(reader, check=None):
    service = native_voice.VoiceRelay(
        Browser(),
        Provider(),
        native_voice.VoiceScope(
            "private-person", "private-workspace", "private-profile", "private-thread"
        ),
        check=check or AsyncMock(),
        thread_status=reader,
    )
    service.ready = True
    return service


async def test_status_feed_is_quiet_and_dedupes_same_snapshot():
    initial = VoiceThreadStatus(typed_turn="finished", awaiting=1)
    applied = VoiceThreadStatus(typed_turn="finished", applied=1)
    reader = AsyncMock(
        side_effect=[
            (initial, ("private-proof",)),
            (initial, ("private-proof",)),
            (applied, ()),
        ]
    )
    service = relay(reader)
    for _ in range(3):
        await service._sync_thread_status()
    assert service.upstream.outbox.qsize() == 2
    pending, completed = [service.upstream.outbox.get_nowait() for _ in range(2)]
    for update in (pending, completed):
        assert update["type"] == "conversation.item.create"
        assert update["item"]["role"] == "system"
        entry = update["item"]["content"][0]
        assert (
            entry["type"] == "input_text"
            and "do not proactively speak" in entry["text"]
        )
        assert "private-" not in str(update)
    assert "1 awaiting review" in pending["item"]["content"][0]["text"]
    assert "1 applied" in completed["item"]["content"][0]["text"]
    assert service.browser.outbox.empty()
    assert reader.await_args_list[0].args == (
        "private-person",
        "private-workspace",
        "private-thread",
        (),
    )
    assert reader.await_args_list[1].args[-1] == ("private-proof",)
    assert service.check.await_count == 2


async def test_status_feed_drops_interrupted_or_unauthorized_update():
    current = VoiceThreadStatus(awaiting=1)
    service = relay(AsyncMock())

    async def late(*_scope):
        await service._interrupt()
        return current, ()

    service.thread_status = late
    await service._sync_thread_status()
    assert service.upstream.outbox.empty()
    assert await service.browser.outbox.get() == {"type": "flush"}

    service.thread_status = AsyncMock(return_value=(current, ()))
    service.check.side_effect = InsufficientPermissionsError("Lost access")
    with pytest.raises(InsufficientPermissionsError):
        await service._sync_thread_status()
    assert service.upstream.outbox.empty()


async def test_status_feed_remains_silent_while_provider_is_speaking_and_caps_updates(
    monkeypatch,
):
    monkeypatch.setattr(native_voice, "MAX_THREAD_STATUS_UPDATES", 2)
    reader = AsyncMock(
        side_effect=[
            (VoiceThreadStatus(awaiting=1), ()),
            (VoiceThreadStatus(applied=1), ()),
        ]
    )
    service = relay(reader)
    service.response_generation = "provider-response"
    await service._sync_thread_status()
    reader.assert_not_awaited()
    service.response_generation = None
    for _ in range(3):
        await service._sync_thread_status()
    assert reader.await_count == 2
    service.upstream.outbox.get_nowait()
    assert "has reached its limit" in str(service.upstream.outbox.get_nowait())
    assert service.upstream.outbox.empty() and service.browser.outbox.empty()


async def test_rejected_optional_context_does_not_end_voice():
    import asyncio

    from tests.test_native_voice import event

    service = relay(AsyncMock(return_value=(VoiceThreadStatus(awaiting=1), ())))
    await service._sync_thread_status()
    sent = service.upstream.outbox.get_nowait()
    reader = asyncio.create_task(service._read_provider())
    try:
        await service.upstream.inbox.put(
            {"type": "error", "error": {"event_id": sent["event_id"]}}
        )
        await service.upstream.inbox.put(
            {"type": "response.created", "response": {"id": "ordinary"}}
        )
        await service.upstream.inbox.put(
            {
                "type": "response.output_audio.delta",
                "response_id": "ordinary",
                "delta": "AAE=",
            }
        )
        assert await event(service.browser.outbox) == b"\x00\x01"
        assert service.thread_status_updates == native_voice.MAX_THREAD_STATUS_UPDATES
        assert service.upstream.outbox.empty()
    finally:
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)


async def test_pending_review_transition_queues_one_verified_announcement():
    """A GUI decision wakes speech only for a previously observed proposal."""
    from app.services.native_voice_thread_status import _ReviewProof

    proof = _ReviewProof(
        ("private-person", "private-workspace", "private-thread"),
        "private-token",
        "create_entry",
        "intent",
        "hash",
    )
    pending = VoiceThreadStatus(awaiting=1, outcomes=((proof, "awaiting"),))
    applied = VoiceThreadStatus(applied=1, outcomes=((proof, "applied"),))
    service = relay(
        AsyncMock(
            side_effect=[(pending, (proof,)), (applied, (proof,)), (applied, (proof,))]
        )
    )
    await service._sync_thread_status()
    assert not service.peer_readouts
    await service._sync_thread_status()
    assert list(service.peer_readouts.values()) == ["Your approved change was applied."]
    await service._sync_thread_status()
    assert len(service.peer_readouts) == 1
    assert "private-token" not in str(service.peer_readouts)
