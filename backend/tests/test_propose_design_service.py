"""integral_propose_design service layer — records/reads/clears the design
marker on a ChatThread, and counts intervening user turns."""

from __future__ import annotations

import pytest

from app.models.edges import CONTAINS
from app.models.nodes import ChatMessage, ChatThread
from app.services import chat_threads

# Must clear the 120-char proposal floor.
_PROPOSAL = (
    "**Demo** app (fresh).\n\n"
    "- **Contacts** — name, email, company\n"
    "- **Deals** — amount, stage, contact relation\n"
    "- Views: Contacts table, Deals kanban\n"
)


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
        user_id="u1",
        session_id="sess-C",
        summary="App X with tracks A, B",
        proposal=_PROPOSAL,
    )
    assert result.get("ok") is True
    assert result.get("_kind") == "design_proposal"
    assert result.get("proposal") == _PROPOSAL.strip()
    reloaded = await ChatThread.get(thread.id)
    assert reloaded.design_proposed is not None
    assert reloaded.design_proposed["proposed_at_user_turn"] == 2
    assert reloaded.design_proposed["summary"] == "App X with tracks A, B"
    assert reloaded.design_proposed["proposal"] == _PROPOSAL.strip()


@pytest.mark.asyncio
async def test_record_design_proposed_requires_proposal_body():
    await _thread_with_user_turns("sess-C2", 1)
    result = await chat_threads.record_design_proposed(
        user_id="u1", session_id="sess-C2", summary="App X", proposal="too short"
    )
    assert result.get("error") == "proposal_required"


@pytest.mark.asyncio
async def test_record_design_proposed_fails_closed_without_session():
    result = await chat_threads.record_design_proposed(
        user_id="u1", session_id=None, summary="x", proposal=_PROPOSAL
    )
    assert result.get("error") == "session_required"


@pytest.mark.asyncio
async def test_record_design_proposed_rejects_foreign_thread():
    await _thread_with_user_turns("sess-D", 1)
    result = await chat_threads.record_design_proposed(
        user_id="someone_else",
        session_id="sess-D",
        summary="x",
        proposal=_PROPOSAL,
    )
    assert result.get("error") == "forbidden"


@pytest.mark.asyncio
async def test_record_design_proposed_preserves_earliest_turn_on_repropose_same_turn():
    """Re-propose before the user replies keeps proposed_at_user_turn."""
    thread = await _thread_with_user_turns("sess-E0", 1)
    await chat_threads.record_design_proposed(
        user_id="u1",
        session_id="sess-E0",
        summary="first",
        proposal=_PROPOSAL,
    )
    await chat_threads.record_design_proposed(
        user_id="u1",
        session_id="sess-E0",
        summary="second (same turn)",
        proposal=_PROPOSAL + "\n(tweaked)",
    )
    reloaded = await ChatThread.get(thread.id)
    assert reloaded.design_proposed["proposed_at_user_turn"] == 1
    assert reloaded.design_proposed["summary"] == "second (same turn)"


@pytest.mark.asyncio
async def test_record_design_proposed_refuses_repropose_after_user_reply():
    """On the confirm/build turn, re-propose must fail — go straight to build."""
    thread = await _thread_with_user_turns("sess-E", 1)
    await chat_threads.record_design_proposed(
        user_id="u1",
        session_id="sess-E",
        summary="first",
        proposal=_PROPOSAL,
    )
    msg = await ChatMessage.create(role="user", thread_id=thread.id)
    await thread.connect(msg, edge=CONTAINS)

    result = await chat_threads.record_design_proposed(
        user_id="u1",
        session_id="sess-E",
        summary="second (re-propose)",
        proposal=_PROPOSAL,
    )
    assert result.get("error") == "already_proposed"
    reloaded = await ChatThread.get(thread.id)
    assert reloaded.design_proposed["summary"] == "first"


@pytest.mark.asyncio
async def test_design_awaiting_user_response_true_until_reply():
    thread = await _thread_with_user_turns("sess-F", 1)
    assert await chat_threads.design_awaiting_user_response("sess-F") is False
    await chat_threads.record_design_proposed(
        user_id="u1",
        session_id="sess-F",
        summary="x",
        proposal=_PROPOSAL,
    )
    assert await chat_threads.design_awaiting_user_response("sess-F") is True
    msg = await ChatMessage.create(role="user", thread_id=thread.id)
    await thread.connect(msg, edge=CONTAINS)
    assert await chat_threads.design_awaiting_user_response("sess-F") is False
