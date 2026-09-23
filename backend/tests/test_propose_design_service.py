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
    assert result.get("_kind") == "design_outline"
    assert result.get("proposal") == _PROPOSAL.strip()
    reloaded = await ChatThread.get(thread.id)
    assert reloaded.design_proposed is not None
    assert reloaded.design_proposed["proposed_at_user_turn"] == 2
    assert reloaded.design_proposed["summary"] == "App X with tracks A, B"
    assert reloaded.design_proposed["proposal"] == _PROPOSAL.strip()


@pytest.mark.asyncio
async def test_existing_app_proposal_binds_target_id():
    thread = await _thread_with_user_turns("existing-app-design", 1)
    result = await chat_threads.record_design_proposed(
        user_id="u1",
        session_id="existing-app-design",
        summary="Add Wiki track to Car Rental Manager",
        proposal=_PROPOSAL,
        target_app_id="n.WorkspaceApp.approved",
    )
    assert result.get("ok") is True
    reloaded = await ChatThread.get(thread.id)
    assert reloaded.design_proposed["target_app_id"] == "n.WorkspaceApp.approved"


@pytest.mark.asyncio
async def test_record_design_proposed_persists_acceptance_assertions():
    from app.services import chat_threads

    await _thread_with_user_turns("acceptance-assertions", 1)
    result = await chat_threads.record_design_proposed(
        user_id="u1",
        session_id="acceptance-assertions",
        summary="Rental app",
        proposal=_PROPOSAL,
        acceptance_assertions=["Cars has registration", "Rentals links a car"],
    )

    assert result["acceptance_assertions"] == [
        "Cars has registration",
        "Rentals links a car",
    ]
    thread = await chat_threads.get_thread_by_session("acceptance-assertions")
    assert (
        thread.design_proposed["acceptance_assertions"]
        == result["acceptance_assertions"]
    )


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
async def test_record_design_proposed_allows_amend_after_user_reply():
    """Correction turn may replace a pending (unapproved) design."""
    thread = await _thread_with_user_turns("sess-E", 1)
    await chat_threads.record_design_proposed(
        user_id="u1",
        session_id="sess-E",
        summary="first",
        proposal=_PROPOSAL,
    )
    msg = await ChatMessage.create(
        role="user",
        thread_id=thread.id,
        parts=[
            {"type": "text", "text": "Please alter that design: add a Customers track."}
        ],
    )
    await thread.connect(msg, edge=CONTAINS)

    result = await chat_threads.record_design_proposed(
        user_id="u1",
        session_id="sess-E",
        summary="second (amend Customers)",
        proposal=_PROPOSAL + "\n- **Customers** — name, email\n",
    )
    assert result.get("ok") is True
    assert result.get("replaced") is True
    reloaded = await ChatThread.get(thread.id)
    assert reloaded.design_proposed["summary"] == "second (amend Customers)"
    assert reloaded.design_proposed.get("approved") is False
    assert reloaded.design_proposed["proposed_at_user_turn"] == 2


@pytest.mark.asyncio
async def test_record_design_proposed_refuses_repropose_after_approved():
    """Once blessed, re-propose must fail — go straight to build."""
    thread = await _thread_with_user_turns("sess-E-apr", 1)
    await chat_threads.record_design_proposed(
        user_id="u1",
        session_id="sess-E-apr",
        summary="first",
        proposal=_PROPOSAL,
    )
    thread = await ChatThread.get(thread.id)
    marker = dict(thread.design_proposed or {})
    marker["approved"] = True
    thread.design_proposed = marker
    await thread.save()

    result = await chat_threads.record_design_proposed(
        user_id="u1",
        session_id="sess-E-apr",
        summary="second",
        proposal=_PROPOSAL,
    )
    assert result.get("error") == "already_proposed"
    reloaded = await ChatThread.get(thread.id)
    assert reloaded.design_proposed["summary"] == "first"


@pytest.mark.asyncio
async def test_record_design_proposed_refuses_affirm_without_correction():
    """Pure affirm after a pending design must not re-propose — build instead."""
    thread = await _thread_with_user_turns("sess-E-aff", 1)
    await chat_threads.record_design_proposed(
        user_id="u1",
        session_id="sess-E-aff",
        summary="first",
        proposal=_PROPOSAL,
    )
    msg = await ChatMessage.create(
        role="user",
        thread_id=thread.id,
        parts=[
            {
                "type": "text",
                "text": "Yes — use the revised design. Stage the build for approval.",
            }
        ],
    )
    await thread.connect(msg, edge=CONTAINS)

    result = await chat_threads.record_design_proposed(
        user_id="u1",
        session_id="sess-E-aff",
        summary="second (same shape)",
        proposal=_PROPOSAL + "\n",
    )
    assert result.get("error") == "affirm_build_instead"
    reloaded = await ChatThread.get(thread.id)
    assert reloaded.design_proposed["summary"] == "first"


@pytest.mark.asyncio
async def test_looks_like_design_affirm_helpers():
    assert chat_threads.looks_like_design_affirm("Yes, build it")
    assert chat_threads.looks_like_design_affirm("looks good — stage the build")
    assert not chat_threads.looks_like_design_affirm(
        "Please alter that design: drop the Service track"
    )
    assert not chat_threads.looks_like_design_affirm(
        "Also I want to track when cars get damaged. And each car has a "
        "daily rate - sometimes USD, sometimes GYD. I don't need a whole "
        "separate place for service stuff, just keep the service and "
        "document dates on the car itself."
    )
    assert not chat_threads.looks_like_design_affirm("")


@pytest.mark.asyncio
async def test_design_amend_required_after_correction_reply():
    """Non-affirm reply while design pending → amend gate open."""
    thread = await _thread_with_user_turns("sess-amend-req", 1)
    await chat_threads.record_design_proposed(
        user_id="u1",
        session_id="sess-amend-req",
        summary="cars",
        proposal=_PROPOSAL,
    )
    assert await chat_threads.design_amend_required("sess-amend-req") is False
    msg = await ChatMessage.create(
        role="user",
        thread_id=thread.id,
        parts=[
            {
                "type": "text",
                "text": (
                    "Also I want to track when cars get damaged. "
                    "I don't need a separate service track."
                ),
            }
        ],
    )
    await thread.connect(msg, edge=CONTAINS)
    assert await chat_threads.design_amend_required("sess-amend-req") is True


@pytest.mark.asyncio
async def test_design_amend_required_false_on_affirm():
    thread = await _thread_with_user_turns("sess-amend-aff", 1)
    await chat_threads.record_design_proposed(
        user_id="u1",
        session_id="sess-amend-aff",
        summary="cars",
        proposal=_PROPOSAL,
    )
    msg = await ChatMessage.create(
        role="user",
        thread_id=thread.id,
        parts=[{"type": "text", "text": "Yes, build it"}],
    )
    await thread.connect(msg, edge=CONTAINS)
    assert await chat_threads.design_amend_required("sess-amend-aff") is False


def test_pending_design_context_for_utterance_on_correction():
    marker = {
        "proposed_at_user_turn": 1,
        "approved": False,
        "proposal": _PROPOSAL + "\n- **Service** — dates on a separate track\n",
    }
    prior = chat_threads.pending_design_context_for_utterance(
        marker=marker,
        user_turns_before_this_message=1,
        utterance="Also I want a damage field on each car.",
    )
    assert "Service" in prior
    assert "integral_propose_design" not in prior  # data only, not tutoring
    assert (
        chat_threads.pending_design_context_for_utterance(
            marker=marker,
            user_turns_before_this_message=1,
            utterance="Yes, build it",
        )
        == ""
    )


@pytest.mark.asyncio
async def test_stamp_design_approved_on_affirm():
    thread = await _thread_with_user_turns("sess-stamp", 1)
    await chat_threads.record_design_proposed(
        user_id="u1",
        session_id="sess-stamp",
        summary="cars",
        proposal=_PROPOSAL,
    )
    thread = await ChatThread.get(thread.id)
    assert await chat_threads.stamp_design_approved(
        thread=thread, utterance="Looks good.. Please proceed"
    )
    reloaded = await ChatThread.get(thread.id)
    assert reloaded.design_proposed["approved"] is True
    assert reloaded.design_proposed.get("approved_via") == "chat_affirm"


@pytest.mark.asyncio
async def test_design_chat_affirmed_for_build():
    thread = await _thread_with_user_turns("sess-chat-aff", 1)
    await chat_threads.record_design_proposed(
        user_id="u1",
        session_id="sess-chat-aff",
        summary="cars",
        proposal=_PROPOSAL,
    )
    assert await chat_threads.design_chat_affirmed_for_build("sess-chat-aff") is False
    msg = await ChatMessage.create(
        role="user",
        thread_id=thread.id,
        parts=[{"type": "text", "text": "Looks good.. Please proceed"}],
    )
    await thread.connect(msg, edge=CONTAINS)
    assert await chat_threads.design_chat_affirmed_for_build("sess-chat-aff") is True


@pytest.mark.asyncio
async def test_applied_design_receipt_closes_build_authorization():
    thread = await _thread_with_user_turns("sess-applied-design", 1)
    await chat_threads.record_design_proposed(
        user_id="u1",
        session_id="sess-applied-design",
        summary="cars",
        proposal=_PROPOSAL,
    )
    thread = await ChatThread.get(thread.id)
    assert await chat_threads.stamp_design_approved(
        thread=thread, utterance="Looks good. Build it."
    )
    assert await chat_threads.design_chat_affirmed_for_build("sess-applied-design")
    assert not await chat_threads.record_design_build_receipt(
        session_id="sess-applied-design", user_id="someone-else", batch_token="token-1"
    )
    assert await chat_threads.record_design_build_receipt(
        session_id="sess-applied-design", user_id="u1", batch_token="token-1"
    )
    assert not await chat_threads.record_design_build_receipt(
        session_id="sess-applied-design", user_id="u1", batch_token="token-2"
    )
    assert not await chat_threads.design_chat_affirmed_for_build("sess-applied-design")
    assert not await chat_threads.design_proposed_pending("sess-applied-design")
    reloaded = await ChatThread.get(thread.id)
    assert reloaded.design_proposed["build_receipt"]["batch_token"] == "token-1"

    # The same turn cannot rewrite the design, but a new user request can
    # begin a fresh proposal in this thread after the first App was applied.
    refused = await chat_threads.record_design_proposed(
        user_id="u1",
        session_id="sess-applied-design",
        summary="repairs",
        proposal=_PROPOSAL.replace("cars", "repairs"),
    )
    assert refused["error"] == "already_proposed"
    followup = await ChatMessage.create(
        role="user",
        thread_id=thread.id,
        parts=[{"type": "text", "text": "I need another app for repairs."}],
    )
    await thread.connect(followup, edge=CONTAINS)
    new_design = await chat_threads.record_design_proposed(
        user_id="u1",
        session_id="sess-applied-design",
        summary="repairs",
        proposal=_PROPOSAL.replace("cars", "repairs"),
    )
    assert new_design["ok"] is True
    assert new_design["replaced"] is False
    assert not (await ChatThread.get(thread.id)).design_proposed.get("build_receipt")


@pytest.mark.asyncio
async def test_partial_design_receipt_is_owner_bound_and_durable():
    thread = await _thread_with_user_turns("sess-partial-design", 1)
    await chat_threads.record_design_proposed(
        user_id="u1",
        session_id="sess-partial-design",
        summary="cars",
        proposal=_PROPOSAL,
    )
    thread = await ChatThread.get(thread.id)
    await chat_threads.stamp_design_approved(thread=thread, utterance="Build it")
    assert not await chat_threads.record_design_partial_build(
        session_id="sess-partial-design", user_id="other", batch_token="partial-1"
    )
    assert await chat_threads.record_design_partial_build(
        session_id="sess-partial-design", user_id="u1", batch_token="partial-1"
    )
    reloaded = await ChatThread.get(thread.id)
    assert reloaded.design_proposed["partial_build"]["batch_token"] == "partial-1"


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
