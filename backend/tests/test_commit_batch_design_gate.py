"""commit_batch refuses to mint a greenfield-scaffold card (a batch with a
create_app / author_profile op) unless a design was proposed with an intervening user turn."""

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



def _author_profile_op():
    return {
        "kind": "author_profile",
        "summary": 'Author library profile "Car Rental"',
        "diff_human": "Author profile",
        "diff_machine": {"op": "author_profile"},
        "payload": {"name": "Car Rental", "scope": "app"},
    }

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


def _create_app_track_op(*, with_fields: bool = True):
    payload = {
        "name": "Cars",
        "app_id": "{{app.id}}",
        "description": "Fleet inventory",
    }
    if with_fields:
        payload["entry_types"] = [
            {
                "name": "Car",
                "fields": [
                    {"key": "registration", "name": "Registration", "type": "text"},
                    {"key": "daily_rate", "name": "Daily Rate", "type": "number"},
                ],
            }
        ]
    return {
        "kind": "create_app_track",
        "summary": "Create track Cars",
        "diff_human": "Create track Cars",
        "diff_machine": {"op": "create_app_track"},
        "payload": payload,
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
async def test_author_profile_batch_refused_without_marker(
    bind_fresh_graph_context_for_async_tests,
):
    """Cold greenfield must not bypass the design card via author_profile-only."""
    await _thread("s-ap", 1, marker=None)
    await open_batch(user_id="u1", session_id="s-ap", label="build")
    await append_to_batch(user_id="u1", session_id="s-ap", op=_author_profile_op())
    with pytest.raises(StagingError) as ei:
        await commit_batch(user_id="u1", session_id="s-ap")
    assert ei.value.code == "design_not_proposed"

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
    await append_to_batch(
        user_id="u1",
        session_id="s3",
        op=_create_app_track_op(with_fields=True),
    )
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

@pytest.mark.asyncio
async def test_create_app_without_tracks_refused(
    bind_fresh_graph_context_for_async_tests,
):
    await _thread(
        "s-empty",
        2,
        marker={"proposed_at_user_turn": 1, "summary": "x", "proposal": "y" * 130},
    )
    await open_batch(user_id="u1", session_id="s-empty", label="build")
    await append_to_batch(user_id="u1", session_id="s-empty", op=_create_app_op())
    await append_to_batch(user_id="u1", session_id="s-empty", op=_author_profile_op())
    with pytest.raises(StagingError) as ei:
        await commit_batch(user_id="u1", session_id="s-empty")
    assert ei.value.code == "incomplete_scaffold"


@pytest.mark.asyncio
async def test_tracks_without_fields_refused(
    bind_fresh_graph_context_for_async_tests,
):
    await _thread(
        "s-bare",
        2,
        marker={"proposed_at_user_turn": 1, "summary": "x", "proposal": "y" * 130},
    )
    await open_batch(user_id="u1", session_id="s-bare", label="build")
    await append_to_batch(user_id="u1", session_id="s-bare", op=_create_app_op())
    await append_to_batch(
        user_id="u1", session_id="s-bare", op=_create_app_track_op(with_fields=False)
    )
    with pytest.raises(StagingError) as ei:
        await commit_batch(user_id="u1", session_id="s-bare")
    assert ei.value.code == "incomplete_scaffold"


@pytest.mark.asyncio
async def test_create_app_with_shaped_tracks_allowed(
    bind_fresh_graph_context_for_async_tests,
):
    await _thread(
        "s-ok",
        2,
        marker={"proposed_at_user_turn": 1, "summary": "x", "proposal": "y" * 130},
    )
    await open_batch(user_id="u1", session_id="s-ok", label="build")
    await append_to_batch(user_id="u1", session_id="s-ok", op=_create_app_op())
    await append_to_batch(
        user_id="u1", session_id="s-ok", op=_create_app_track_op(with_fields=True)
    )
    sc = await commit_batch(user_id="u1", session_id="s-ok")
    assert sc is not None
    assert sc.kind == "batch"

