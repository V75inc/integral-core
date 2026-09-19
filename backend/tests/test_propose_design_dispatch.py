"""integral_propose_design records the thread marker AND mints a StagedChange."""

from __future__ import annotations

import pytest

from app.agentive.tooling.dispatch import dispatch_tool
from app.models.edges import CONTAINS
from app.models.nodes import ChatMessage, ChatThread

_PROPOSAL = (
    "**Demo** app (fresh).\n\n"
    "- **Contacts** — name, email, company\n"
    "- **Deals** — amount, stage, contact relation\n"
    "- Views: Contacts table, Deals kanban\n"
)


@pytest.fixture(autouse=True)
def _reset_staging():
    from app.agentive.staging import _reset_for_tests

    _reset_for_tests()
    yield
    _reset_for_tests()


async def _thread(session_id: str, n_user: int, user_id: str = "u1") -> ChatThread:
    t = await ChatThread.create(user_id=user_id, provider_session_id=session_id)
    for _ in range(n_user):
        m = await ChatMessage.create(role="user", thread_id=t.id)
        await t.connect(m, edge=CONTAINS)
    return t


async def _approve_design(session_id: str, token: str, user_id: str = "u1") -> None:
    """Bless the design token (marker.approved) — no Prompt Sheet for design."""
    from app.agentive.services.staging_apply import bless_and_execute

    out = await bless_and_execute(user_id=user_id, token=token)
    assert out.get("ok") is True, out


@pytest.mark.asyncio
async def test_dispatch_propose_design_records_marker(
    bind_fresh_graph_context_for_async_tests,
):
    """Dispatching with a live session_id records the design_proposed marker."""
    thread = await _thread("sess-PD", 1, user_id="u1")
    res = await dispatch_tool(
        "integral_propose_design",
        {"summary": "App X: tracks A, B", "proposal": _PROPOSAL},
        principal_id="u1",
        scope="ws1",
        session_id="sess-PD",
    )
    assert not res.is_error, res
    assert res.data.get("_kind") == "staged_change"
    assert res.data.get("kind") == "design_proposal"
    assert res.data.get("token")
    assert res.data.get("idempotency_key")
    assert res.data.get("proposal")
    reloaded = await ChatThread.get(thread.id)
    assert reloaded.design_proposed is not None
    assert reloaded.design_proposed["proposed_at_user_turn"] == 1
    # Design confirmation is conversational — Prompt Sheet stays closed until
    # the later build batch is committed.
    from app.services.prompt_queue import get_queue, queue_is_open

    assert queue_is_open(reloaded) is False
    queue = get_queue(reloaded)
    writes = [
        i
        for i in (queue.get("items") or [])
        if i.get("kind") == "staged_write" and i.get("write_kind") == "design_proposal"
    ]
    assert writes == []


@pytest.mark.asyncio
async def test_dispatch_propose_design_is_idempotent_same_summary(
    bind_fresh_graph_context_for_async_tests,
):
    """Same session + summary returns the same staged token."""
    await _thread("sess-PD-idem", 1, user_id="u1")
    first = await dispatch_tool(
        "integral_propose_design",
        {"summary": "App X: tracks A, B", "proposal": _PROPOSAL},
        principal_id="u1",
        scope="ws1",
        session_id="sess-PD-idem",
    )
    second = await dispatch_tool(
        "integral_propose_design",
        {"summary": "App X: tracks A, B", "proposal": _PROPOSAL},
        principal_id="u1",
        scope="ws1",
        session_id="sess-PD-idem",
    )
    assert not first.is_error, first
    assert not second.is_error, second
    assert first.data["token"] == second.data["token"]
    assert first.data["idempotency_key"] == second.data["idempotency_key"]


@pytest.mark.asyncio
async def test_dispatch_propose_design_no_session_fails(
    bind_fresh_graph_context_for_async_tests,
):
    """Fail closed when no session_id is supplied — nothing to key the marker."""
    res = await dispatch_tool(
        "integral_propose_design",
        {"summary": "x", "proposal": _PROPOSAL},
        principal_id="u1",
        scope="ws1",
        session_id=None,
    )
    assert res.is_error


@pytest.mark.asyncio
async def test_dispatch_refuses_tools_while_design_awaiting_user(
    bind_fresh_graph_context_for_async_tests,
):
    """After propose, begin_batch (and other tools) are refused until user reply."""
    await _thread("sess-halt", 1, user_id="u1")
    propose = await dispatch_tool(
        "integral_propose_design",
        {"summary": "App X", "proposal": _PROPOSAL},
        principal_id="u1",
        scope="ws1",
        session_id="sess-halt",
    )
    assert not propose.is_error, propose

    blocked = await dispatch_tool(
        "integral_begin_batch",
        {"label": "Build"},
        principal_id="u1",
        scope="ws1",
        session_id="sess-halt",
    )
    assert blocked.is_error
    assert blocked.error_code == "design_awaiting_user"


@pytest.mark.asyncio
async def test_dispatch_create_without_batch_refuses_after_user_confirms(
    bind_fresh_graph_context_for_async_tests,
):
    """Confirm turn: lone create_track must not mint a Prompt Sheet card.

    User reply advances turn count past proposed_at → design_awaiting clears,
    but design_proposed stays until commit_batch. Staging outside a batch must
    fail closed with batch_required.
    """
    thread = await _thread("sess-batch-req", 1, user_id="u1")
    propose = await dispatch_tool(
        "integral_propose_design",
        {"summary": "Car Rental", "proposal": _PROPOSAL},
        principal_id="u1",
        scope="ws1",
        session_id="sess-batch-req",
    )
    assert not propose.is_error, propose
    await _approve_design("sess-batch-req", propose.data["token"])

    # Simulate follow-on user turn after design approve
    m = await ChatMessage.create(role="user", thread_id=thread.id)
    await thread.connect(m, edge=CONTAINS)

    lone = await dispatch_tool(
        "integral_create_track",
        {"name": "Cars", "description": "Fleet of cars available for rent"},
        principal_id="u1",
        scope="ws1",
        session_id="sess-batch-req",
    )
    assert lone.is_error
    assert lone.error_code == "batch_required"


@pytest.mark.asyncio
async def test_dispatch_scaffold_batch_refuses_track_without_app(
    bind_fresh_graph_context_for_async_tests,
):
    """Inside an open scaffold batch, create_track still needs app_id."""
    thread = await _thread("sess-track-app", 1, user_id="u1")
    propose = await dispatch_tool(
        "integral_propose_design",
        {"summary": "Car Rental", "proposal": _PROPOSAL},
        principal_id="u1",
        scope="ws1",
        session_id="sess-track-app",
    )
    assert not propose.is_error, propose
    await _approve_design("sess-track-app", propose.data["token"])

    m = await ChatMessage.create(role="user", thread_id=thread.id)
    await thread.connect(m, edge=CONTAINS)

    opened = await dispatch_tool(
        "integral_begin_batch",
        {"label": "Car Rental"},
        principal_id="u1",
        scope="ws1",
        session_id="sess-track-app",
    )
    assert not opened.is_error, opened

    orphan = await dispatch_tool(
        "integral_create_track",
        {"name": "Cars", "description": "Fleet of cars available for rent"},
        principal_id="u1",
        scope="ws1",
        session_id="sess-track-app",
    )
    assert orphan.is_error
    assert orphan.error_code == "scaffold_track_requires_app"


@pytest.mark.asyncio
async def test_bless_design_proposal_marks_approved(
    bind_fresh_graph_context_for_async_tests,
):
    """Approve executes the design_proposal kind and stamps the thread marker."""
    from app.agentive.services.staging_apply import bless_and_execute

    thread = await _thread("sess-PD-bless", 1, user_id="u1")
    propose = await dispatch_tool(
        "integral_propose_design",
        {"summary": "Assets app", "proposal": _PROPOSAL},
        principal_id="u1",
        scope="ws1",
        session_id="sess-PD-bless",
    )
    assert not propose.is_error, propose
    token = propose.data["token"]
    out = await bless_and_execute(user_id="u1", token=token)
    assert out.get("ok") is True
    assert out.get("consumed") is True
    exec_result = out.get("execute_result") or {}
    assert exec_result.get("approved") is True
    assert exec_result.get("needs_agent_build") is True
    reloaded = await ChatThread.get(thread.id)
    assert reloaded.design_proposed["approved"] is True
    assert reloaded.design_proposed.get("idempotency_key")
