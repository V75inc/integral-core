"""Committed native SSE delivery, independent of model execution."""

from unittest.mock import AsyncMock

import pytest

from app.schemas.agentive.chat_events import ChatEventReplayPage
from app.services import chat_turn_streaming


def stream(scope_check=None, **extra):
    return chat_turn_streaming.stream_committed_chat_turn(
        principal_id="owner",
        workspace_id="workspace",
        thread_id="thread",
        work_item_id="work",
        assert_scope=scope_check or AsyncMock(),
        poll_seconds=0,
        **extra,
    )


@pytest.mark.asyncio
async def test_stream_emits_committed_ids_and_settles_only_after_terminal_commit(
    monkeypatch,
):
    replay = AsyncMock(
        side_effect=[
            ChatEventReplayPage(
                events=[{"sequence": 3, "type": "text-delta", "delta": "Ready"}],
                committed_through=3,
                next_after_sequence=3,
                work_status="running",
            ),
            ChatEventReplayPage(
                events=[{"sequence": 4, "type": "message-finish"}],
                committed_through=4,
                next_after_sequence=4,
                work_status="succeeded",
            ),
        ]
    )
    scope = AsyncMock()
    monkeypatch.setattr(chat_turn_streaming, "replay_work_item_chat_events", replay)
    frames = [part async for part in stream(scope, after_sequence=2)]
    assert frames[0].startswith(b"id: 3\n")
    assert frames[1].startswith(b"id: 4\n")
    assert b"turn-settled" not in frames[0] + frames[1]
    assert b"turn-settled" in frames[2]
    assert b'"status": "succeeded"' in frames[2]
    assert replay.await_args_list[1].kwargs["after_sequence"] == 3
    assert scope.await_count == 2


@pytest.mark.asyncio
async def test_scope_revocation_prevents_next_committed_page(monkeypatch):
    scope = AsyncMock(side_effect=[None, PermissionError("revoked")])
    replay = AsyncMock(
        return_value=ChatEventReplayPage(
            events=[{"sequence": 1, "type": "text-delta", "delta": "Allowed"}],
            committed_through=1,
            next_after_sequence=1,
            work_status="running",
        )
    )
    monkeypatch.setattr(chat_turn_streaming, "replay_work_item_chat_events", replay)
    response = stream(scope)
    assert b"Allowed" in await anext(response)
    with pytest.raises(PermissionError):
        await anext(response)
    assert replay.await_count == 1


@pytest.mark.asyncio
async def test_cursor_gap_never_delivers_untrusted_page(monkeypatch):
    replay = AsyncMock(
        return_value=ChatEventReplayPage(
            events=[{"sequence": 5, "type": "text-delta", "delta": "Must not emit"}],
            gap=True,
            committed_through=5,
            work_status="succeeded",
        )
    )
    monkeypatch.setattr(chat_turn_streaming, "replay_work_item_chat_events", replay)
    frames = b"".join([part async for part in stream()])
    assert b"chat_event_sequence_gap" in frames
    assert b"Must not emit" not in frames


@pytest.mark.asyncio
async def test_disconnect_closes_only_delivery(monkeypatch):
    replay = AsyncMock(
        return_value=ChatEventReplayPage(
            events=[{"sequence": 1, "type": "text-delta", "delta": "Saved"}],
            committed_through=1,
            work_status="running",
        )
    )
    monkeypatch.setattr(chat_turn_streaming, "replay_work_item_chat_events", replay)
    response = stream()
    assert b"Saved" in await anext(response)
    await response.aclose()
    assert replay.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["failed", "expired", "dead_letter"])
async def test_terminal_without_provider_output_is_not_success(monkeypatch, status):
    monkeypatch.setattr(
        chat_turn_streaming,
        "replay_work_item_chat_events",
        AsyncMock(
            return_value=ChatEventReplayPage(work_status=status),
        ),
    )
    frames = b"".join([part async for part in stream()])
    assert b"event: error" in frames
    assert f'"status": "{status}"'.encode() in frames
