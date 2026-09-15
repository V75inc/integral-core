"""integral_propose_design service layer — records/reads/clears the design
marker on a ChatThread, and counts intervening user turns."""

from __future__ import annotations

import pytest

from app.models.edges import CONTAINS
from app.models.nodes import ChatMessage, ChatThread
from app.services import chat_threads


async def _thread_with_user_turns(session_id: str, n_user: int) -> ChatThread:
    thread = await ChatThread.create(user_id="u1", provider_session_id=session_id)
    for i in range(n_user):
        msg = await ChatMessage.create(role="user", thread_id=thread.id)
        await thread.connect(msg, edge=CONTAINS)
    return thread


@pytest.mark.asyncio
async def test_get_thread_by_session_finds_it():
    thread = await _thread_with_user_turns("sess-A", 1)
    found = await chat_threads.get_thread_by_session("sess-A")
    assert found is not None
    assert found.id == thread.id


@pytest.mark.asyncio
async def test_count_user_turns():
    thread = await _thread_with_user_turns("sess-B", 3)
    amsg = await ChatMessage.create(role="assistant", thread_id=thread.id)
    await thread.connect(amsg, edge=CONTAINS)
    assert await chat_threads.count_user_turns(thread) == 3


@pytest.mark.asyncio
async def test_record_design_proposed_writes_marker_with_current_turn():
    thread = await _thread_with_user_turns("sess-C", 2)
    result = await chat_threads.record_design_proposed(
        user_id="u1", session_id="sess-C", summary="App X with tracks A, B"
    )
    assert result.get("ok") is True
    reloaded = await ChatThread.get(thread.id)
    assert reloaded.design_proposed is not None
    assert reloaded.design_proposed["proposed_at_user_turn"] == 2
    assert reloaded.design_proposed["summary"] == "App X with tracks A, B"


@pytest.mark.asyncio
async def test_record_design_proposed_fails_closed_without_session():
    result = await chat_threads.record_design_proposed(
        user_id="u1", session_id=None, summary="x"
    )
    assert result.get("error") == "session_required"


@pytest.mark.asyncio
async def test_record_design_proposed_rejects_foreign_thread():
    await _thread_with_user_turns("sess-D", 1)
    result = await chat_threads.record_design_proposed(
        user_id="someone_else", session_id="sess-D", summary="x"
    )
    assert result.get("error") == "forbidden"


@pytest.mark.asyncio
async def test_record_design_proposed_preserves_earliest_turn_on_repropose():
    """A model that re-proposes on the confirmation turn (before building)
    must not advance proposed_at_user_turn to the current turn — that would
    reset the intervening-turn baseline and make the gate spuriously refuse."""
    thread = await _thread_with_user_turns("sess-E", 1)
    # First proposal at user-turn 1.
    await chat_threads.record_design_proposed(
        user_id="u1", session_id="sess-E", summary="first"
    )
    reloaded = await ChatThread.get(thread.id)
    assert reloaded.design_proposed["proposed_at_user_turn"] == 1

    # User replies "yes" -> a second user turn -> now 2 user messages.
    msg = await ChatMessage.create(role="user", thread_id=thread.id)
    await thread.connect(msg, edge=CONTAINS)

    # Model re-proposes on this confirm turn. proposed_at_user_turn must STAY 1
    # (the original), not advance to 2, so the gate's current(2) > 1 still holds.
    await chat_threads.record_design_proposed(
        user_id="u1", session_id="sess-E", summary="second (re-propose)"
    )
    reloaded = await ChatThread.get(thread.id)
    assert reloaded.design_proposed["proposed_at_user_turn"] == 1
    # summary is still refreshed to the latest proposal text.
    assert reloaded.design_proposed["summary"] == "second (re-propose)"
