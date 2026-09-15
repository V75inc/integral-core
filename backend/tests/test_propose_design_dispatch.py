"""integral_propose_design dispatches ephemerally with the live session_id and
records the marker — never mints a StagedChange."""

from __future__ import annotations

import pytest

from app.agentive.tooling.dispatch import dispatch_tool
from app.models.edges import CONTAINS
from app.models.nodes import ChatMessage, ChatThread


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
        {"summary": "App X: tracks A, B"},
        principal_id="u1",
        scope="ws1",
        session_id="sess-PD",
    )
    assert not res.is_error, res
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
        {"summary": "x"},
        principal_id="u1",
        scope="ws1",
        session_id=None,
    )
    assert res.is_error
