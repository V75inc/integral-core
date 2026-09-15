"""commit_batch refuses to mint a greenfield-scaffold card (a batch with a
create_app op) unless a design was proposed with an intervening user turn."""

from __future__ import annotations

import pytest

from app.agentive import staging
from app.agentive.staging import (
    StagingError,
    append_to_batch,
    commit_batch,
    open_batch,
)
from app.models.edges import CONTAINS
from app.models.nodes import ChatMessage, ChatThread


@pytest.fixture(autouse=True)
def _reset():
    staging._reset_for_tests()
    yield
    staging._reset_for_tests()


async def _thread(session_id, n_user, marker=None):
    t = await ChatThread.create(user_id="u1", provider_session_id=session_id)
    for _ in range(n_user):
        m = await ChatMessage.create(role="user", thread_id=t.id)
        await t.connect(m, edge=CONTAINS)
    if marker is not None:
        t.design_proposed = marker
        await t.save()
    return t


def _create_app_op():
    return {
        "kind": "create_app",
        "summary": "Create app Foo",
        "diff_human": "Create app Foo",
        "diff_machine": {"op": "create_app"},
        "payload": {"name": "Foo"},
    }


def _create_track_op():
    return {
        "kind": "create_track",
        "summary": "Create track Bar",
        "diff_human": "Create track Bar",
        "diff_machine": {"op": "create_track"},
        "payload": {"name": "Bar", "app_id": "{{app.id}}"},
    }


@pytest.mark.asyncio
async def test_greenfield_batch_refused_without_marker(
    bind_fresh_graph_context_for_async_tests,
):
    await _thread("s1", 1, marker=None)
    await open_batch(user_id="u1", session_id="s1", label="build")
    await append_to_batch(user_id="u1", session_id="s1", op=_create_app_op())
    await append_to_batch(user_id="u1", session_id="s1", op=_create_track_op())
    with pytest.raises(StagingError):
        await commit_batch(user_id="u1", session_id="s1")


@pytest.mark.asyncio
async def test_greenfield_batch_refused_without_intervening_turn(
    bind_fresh_graph_context_for_async_tests,
):
    await _thread("s2", 2, marker={"proposed_at_user_turn": 2, "summary": "x"})
    await open_batch(user_id="u1", session_id="s2", label="build")
    await append_to_batch(user_id="u1", session_id="s2", op=_create_app_op())
    with pytest.raises(StagingError):
        await commit_batch(user_id="u1", session_id="s2")


@pytest.mark.asyncio
async def test_greenfield_batch_allowed_with_marker_and_turn(
    bind_fresh_graph_context_for_async_tests,
):
    thread = await _thread("s3", 3, marker={"proposed_at_user_turn": 2, "summary": "x"})
    await open_batch(user_id="u1", session_id="s3", label="build")
    await append_to_batch(user_id="u1", session_id="s3", op=_create_app_op())
    sc = await commit_batch(user_id="u1", session_id="s3")
    assert sc is not None
    reloaded = await ChatThread.get(thread.id)
    assert reloaded.design_proposed is None


@pytest.mark.asyncio
async def test_non_greenfield_batch_not_gated(
    bind_fresh_graph_context_for_async_tests,
):
    await _thread("s4", 1, marker=None)
    await open_batch(user_id="u1", session_id="s4", label="edit")
    await append_to_batch(user_id="u1", session_id="s4", op=_create_track_op())
    sc = await commit_batch(user_id="u1", session_id="s4")
    assert sc is not None
