"""``integral_ask_user`` — the resident's structured clarifying question.

Covers the service half: recording a question on the thread, the fail-closed
and ownership gates it shares with ``record_design_proposed``, option
normalization, and the two ways a question gets cleared (an explicit answer
from the card, or the user simply typing a reply instead of clicking).

The clobber test is the important one: the same stale-instance hazard that
``test_design_marker_clobber.py`` documents applies here, and its failure mode
is worse — a clobbered question reconciles to "already answered", so the card
goes dead and the user has no way to respond to a question the model is
waiting on.
"""

from __future__ import annotations

from typing import Optional

import pytest

from app.models.edges import CONTAINS
from app.models.nodes import ChatMessage, ChatThread
from app.services import chat_threads
from app.services import prompt_queue as pq

OPTIONS = [
    {"label": "Rebuild", "description": "Start the track from scratch"},
    {"label": "Extend", "description": "Add fields to the existing track"},
]


def _pending_question(thread: Optional[ChatThread]) -> Optional[dict]:
    if thread is None:
        return None
    queue = pq.get_queue(thread)
    for item in queue["items"]:
        if item.get("kind") == "question" and item.get("status") == "pending":
            return {
                "question_id": item.get("id"),
                "asked_at_user_turn": item.get("asked_at_user_turn"),
            }
    return None


async def _thread(session: str, user: str = "u1") -> ChatThread:
    return await ChatThread.create(user_id=user, provider_session_id=session)


@pytest.mark.asyncio
async def test_records_question_and_returns_card_envelope():
    await _thread("sess-ask-1")
    result = await chat_threads.record_pending_question(
        user_id="u1",
        session_id="sess-ask-1",
        question="Rebuild the track or extend it?",
        options=OPTIONS,
        header="Approach",
    )

    # `_kind` is the frontend's discriminator, same convention as staged changes.
    assert result["_kind"] == "user_question"
    assert result["state"] == "pending"
    assert result["header"] == "Approach"
    assert [o["label"] for o in result["options"]] == ["Rebuild", "Extend"]
    assert result["question_id"]

    thread = await chat_threads.get_thread_by_session("sess-ask-1")
    assert thread is not None
    marker = _pending_question(thread) or {}
    assert marker["question_id"] == result["question_id"]
    assert marker["asked_at_user_turn"] == 0


@pytest.mark.asyncio
async def test_fails_closed_without_a_session():
    """External MCP callers carry no session, so there is no thread to ask on."""
    result = await chat_threads.record_pending_question(
        user_id="u1", session_id=None, question="Which one?", options=OPTIONS
    )
    assert result["error"] == "session_required"


@pytest.mark.asyncio
async def test_rejects_a_thread_owned_by_someone_else():
    await _thread("sess-ask-foreign", user="owner")
    result = await chat_threads.record_pending_question(
        user_id="intruder",
        session_id="sess-ask-foreign",
        question="Which one?",
        options=OPTIONS,
    )
    assert result["error"] == "forbidden"


@pytest.mark.asyncio
async def test_rejects_questions_that_are_not_really_choices():
    await _thread("sess-ask-2")

    # One option is not a choice; zero is prose with extra steps.
    single = await chat_threads.record_pending_question(
        user_id="u1",
        session_id="sess-ask-2",
        question="Proceed?",
        options=[{"label": "Yes"}],
    )
    assert single["error"] == "invalid_options"

    blank = await chat_threads.record_pending_question(
        user_id="u1", session_id="sess-ask-2", question="   ", options=OPTIONS
    )
    assert blank["error"] == "invalid_question"

    # Nothing was recorded by either rejection.
    thread = await chat_threads.get_thread_by_session("sess-ask-2")
    assert _pending_question(thread) is None


@pytest.mark.asyncio
async def test_normalizes_the_options_models_actually_emit():
    """Bare strings, blanks, duplicates and overlong lists all get coerced.

    Models emit all of these under load. Dropping the seventh option is a
    better failure than losing the whole question.
    """
    await _thread("sess-ask-3")
    result = await chat_threads.record_pending_question(
        user_id="u1",
        session_id="sess-ask-3",
        question="Pick one",
        options=["Alpha", "  ", "Alpha", {"label": "Beta", "description": "second"}]
        + [{"label": f"Opt{i}"} for i in range(10)],
    )

    labels = [o["label"] for o in result["options"]]
    assert labels[:2] == ["Alpha", "Beta"]  # blank dropped, duplicate collapsed
    assert len(labels) == chat_threads.MAX_QUESTION_OPTIONS
    assert len(set(labels)) == len(labels)
    assert result["options"][1]["description"] == "second"


@pytest.mark.asyncio
async def test_answering_clears_the_marker_and_a_stale_card_cannot():
    await _thread("sess-ask-4")
    asked = await chat_threads.record_pending_question(
        user_id="u1", session_id="sess-ask-4", question="Which?", options=OPTIONS
    )
    thread = await chat_threads.get_thread_by_session("sess-ask-4")
    assert thread is not None

    # A card left open in a background tab, holding a superseded question id.
    stale = await chat_threads.resolve_pending_question(
        user_id="u1", thread=thread, question_id="not-the-live-one"
    )
    assert stale["error"] == "stale_question"
    assert _pending_question(await ChatThread.get(thread.id)) is not None

    answered = await chat_threads.resolve_pending_question(
        user_id="u1",
        thread=thread,
        question_id=asked["question_id"],
        choices=["Extend"],
    )
    assert answered["cleared"] is True
    assert answered["choices"] == ["Extend"]
    assert _pending_question(await ChatThread.get(thread.id)) is None


@pytest.mark.asyncio
async def test_a_typed_reply_clears_the_question_too():
    """Answering in prose instead of clicking must not leave the card live."""
    await _thread("sess-ask-5")
    await chat_threads.record_pending_question(
        user_id="u1", session_id="sess-ask-5", question="Which?", options=OPTIONS
    )

    await chat_threads.clear_pending_question_for_session("sess-ask-5")

    thread = await chat_threads.get_thread_by_session("sess-ask-5")
    assert _pending_question(thread) is None
    assert not pq.queue_is_open(thread) if thread else True


@pytest.mark.asyncio
async def test_assistant_append_does_not_clobber_the_question():
    """Same stale-instance hazard as the design marker, worse failure mode.

    The tool writes the question on its own ChatThread instance mid-turn while
    the turn loop holds an older one. If the end-of-turn assistant append
    persists the stale instance, the question is erased and the card
    reconciles to "already answered" — leaving the user unable to respond to a
    question the model is waiting on.
    """
    thread = await _thread("sess-ask-clobber")
    umsg = await ChatMessage.create(role="user", thread_id=thread.id)
    await thread.connect(umsg, edge=CONTAINS)

    asked = await chat_threads.record_pending_question(
        user_id="u1",
        session_id="sess-ask-clobber",
        question="Rebuild or extend?",
        options=OPTIONS,
    )
    assert asked["_kind"] == "user_question"

    # End of turn: the loop appends the assistant bubble via its stale instance.
    await chat_threads.append_message(
        thread=thread,
        role="assistant",
        parts=[{"type": "text", "text": "Let me know which you'd prefer."}],
    )

    persisted = await ChatThread.get(thread.id)
    assert persisted is not None
    assert (_pending_question(persisted) or {}).get("question_id") == asked[
        "question_id"
    ]


@pytest.mark.asyncio
async def test_dispatch_routes_ask_user_through_the_name_intercept():
    """The tool has an all-None binding, so only the by-name intercept in
    ``_dispatch_propose`` can serve it — it needs the dispatch-context
    session_id that no binding ref carries. If the intercept is removed the
    call falls through to the ``stager is None`` branch and returns
    ``not_implemented`` rather than a question."""
    from app.agentive.tooling.dispatch import dispatch_tool

    await _thread("sess-ask-dispatch")
    result = await dispatch_tool(
        "integral_ask_user",
        {
            "question": "Rebuild or extend?",
            "options": OPTIONS,
            "header": "Approach",
        },
        principal_id="u1",
        scope=None,
        session_id="sess-ask-dispatch",
    )

    assert not result.is_error, result
    assert result.data is not None
    assert result.data["_kind"] == "user_question"
    assert result.data["state"] == "pending"


@pytest.mark.asyncio
async def test_dispatch_surfaces_the_session_gate_as_a_tool_error():
    """An external MCP caller carries no session; the model must see a clean
    error envelope rather than a silent no-op that looks like a asked question."""
    from app.agentive.tooling.dispatch import dispatch_tool

    result = await dispatch_tool(
        "integral_ask_user",
        {"question": "Which?", "options": OPTIONS},
        principal_id="u1",
        scope=None,
        session_id=None,
    )
    assert result.is_error
    assert result.error_code == "session_required"
