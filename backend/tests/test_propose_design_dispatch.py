"""integral_propose_design dispatches ephemerally with the live session_id and
records the marker — never mints a StagedChange."""

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
    assert res.data.get("_kind") == "design_proposal"
    reloaded = await ChatThread.get(thread.id)
    assert reloaded.design_proposed is not None
    assert reloaded.design_proposed["proposed_at_user_turn"] == 1


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

    # Simulate user confirm turn
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
