"""integral_propose_design records the thread marker + blueprint artifact (no card)."""

from __future__ import annotations

import pytest

from app.agentive.tooling.dispatch import dispatch_tool
from app.models.edges import CONTAINS
from app.models.nodes import ChatMessage, ChatThread

_BLUEPRINT = {
    "app": {"id": "app", "name": "Demo"},
    "tracks": [
        {
            "id": "track.contacts",
            "name": "Contacts",
            "entry_types": [
                {
                    "name": "Contact",
                    "fields": [{"key": "email", "name": "Email", "type": "text"}],
                }
            ],
        }
    ],
}

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


async def _approve_design(session_id: str, user_id: str = "u1") -> None:
    """Stamp chat-affirm on the thread marker (no staged design card)."""
    from app.services import chat_threads

    thread = await chat_threads.get_thread_by_session(session_id)
    assert thread is not None
    assert await chat_threads.stamp_design_approved(
        thread=thread, utterance="Looks good, please proceed"
    )


@pytest.mark.asyncio
async def test_dispatch_propose_design_records_marker(
    bind_fresh_graph_context_for_async_tests,
):
    """Dispatching with a live session_id records the design_proposed marker."""
    thread = await _thread("sess-PD", 1, user_id="u1")
    res = await dispatch_tool(
        "integral_propose_design",
        {
            "summary": "App X: tracks A, B",
            "proposal": _PROPOSAL,
            "blueprint": _BLUEPRINT,
        },
        principal_id="u1",
        scope="ws1",
        session_id="sess-PD",
    )
    assert not res.is_error, res
    assert res.data.get("_kind") == "design_outline"
    assert res.data.get("token") is None
    assert res.data.get("proposal")
    assert res.data.get("artifact_key") == "app_design_blueprint"
    reloaded = await ChatThread.get(thread.id)
    assert reloaded.design_proposed is not None
    assert reloaded.design_proposed["proposed_at_user_turn"] == 1
    assert reloaded.design_proposed.get("artifact_key") == "app_design_blueprint"
    arts = reloaded.artifacts or {}
    assert "app_design_blueprint" in arts
    assert arts["app_design_blueprint"]["body"].startswith("**Demo**")
    assert "Contacts" in arts["app_design_blueprint"]["body"]
    # No Prompt Sheet / staged design card.
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
async def test_dispatch_propose_design_replace_updates_artifact(
    bind_fresh_graph_context_for_async_tests,
):
    await _thread("sess-PD-idem", 1, user_id="u1")
    first = await dispatch_tool(
        "integral_propose_design",
        {
            "summary": "App X: tracks A, B",
            "proposal": _PROPOSAL,
            "blueprint": _BLUEPRINT,
        },
        principal_id="u1",
        scope="ws1",
        session_id="sess-PD-idem",
    )
    amended = _PROPOSAL + "\n- **Notes** — free text\n"
    second = await dispatch_tool(
        "integral_propose_design",
        {
            "summary": "App X: tracks A, B, Notes",
            "proposal": amended,
            "blueprint": _BLUEPRINT,
        },
        principal_id="u1",
        scope="ws1",
        session_id="sess-PD-idem",
    )
    assert not first.is_error, first
    assert not second.is_error, second
    assert second.data.get("replaced") is True
    assert second.data.get("artifact_version", 0) >= 2


@pytest.mark.asyncio
async def test_dispatch_create_without_batch_auto_opens_after_user_confirms(
    bind_fresh_graph_context_for_async_tests,
):
    """Confirm turn: create_app without begin_batch auto-opens the scaffold batch."""
    thread = await _thread("sess-batch-req", 1, user_id="u1")
    propose = await dispatch_tool(
        "integral_propose_design",
        {"summary": "Car Rental", "proposal": _PROPOSAL, "blueprint": _BLUEPRINT},
        principal_id="u1",
        scope="ws1",
        session_id="sess-batch-req",
    )
    assert not propose.is_error, propose
    await _approve_design("sess-batch-req")

    m = await ChatMessage.create(role="user", thread_id=thread.id)
    await thread.connect(m, edge=CONTAINS)

    first = await dispatch_tool(
        "integral_create_app",
        {
            "name": "Car Rental Management",
            "description": "Fleet, renters, and rentals",
        },
        principal_id="u1",
        scope="ws1",
        session_id="sess-batch-req",
    )
    assert not first.is_error, first
    assert first.data.get("batched") is True
    assert first.data.get("batch_auto_opened") is True
    assert first.data.get("kind") == "create_app"

    orphan = await dispatch_tool(
        "integral_create_track",
        {"name": "Cars", "description": "Fleet of cars available for rent"},
        principal_id="u1",
        scope="ws1",
        session_id="sess-batch-req",
    )
    assert orphan.is_error
    assert orphan.error_code == "scaffold_track_requires_app"


@pytest.mark.asyncio
async def test_dispatch_create_app_requires_recorded_design_in_chat(
    bind_fresh_graph_context_for_async_tests,
):
    """A prose-only plan cannot turn into a second approval card."""
    await _thread("sess-design-required", 1, user_id="u1")

    result = await dispatch_tool(
        "integral_create_app",
        {"name": "Vehicle Maintenance"},
        principal_id="u1",
        scope="ws1",
        session_id="sess-design-required",
    )

    assert result.is_error
    assert result.error_code == "design_required"
    assert "integral_propose_design" in result.message


@pytest.mark.asyncio
async def test_dispatch_create_app_recovers_an_affirmed_visible_design(
    bind_fresh_graph_context_for_async_tests,
):
    """A model's prose design does not force the user to repeat confirmation."""
    from app.services import chat_threads

    thread = await _thread("sess-visible-design", 0, user_id="u1")
    await chat_threads.append_message(
        thread=thread,
        role="user",
        parts=[{"type": "text", "text": "Build a facilities inspection app."}],
    )
    await chat_threads.append_message(
        thread=thread,
        role="assistant",
        parts=[
            {
                "type": "text",
                "text": (
                    "Facilities Inspection app design:\n"
                    "- Track: Inspections with Asset Name, Inspection Date, and Result fields.\n"
                    "- Views: a table for all inspections and a calendar by Inspection Date."
                ),
            }
        ],
    )
    await chat_threads.append_message(
        thread=thread,
        role="user",
        parts=[{"type": "text", "text": "Confirmed. Build it now."}],
    )

    result = await dispatch_tool(
        "integral_create_app",
        {"name": "Facilities Inspection"},
        principal_id="u1",
        scope="ws1",
        session_id="sess-visible-design",
    )

    assert not result.is_error, result
    assert result.data.get("batched") is True
    reloaded = await ChatThread.get(thread.id)
    assert reloaded.design_proposed is not None
    assert reloaded.design_proposed["approved_via"] == "visible_chat_design_affirm"
    assert (reloaded.artifacts or {})["app_design_blueprint"]["metadata"]["source"] == (
        "visible_chat_design_affirm"
    )


@pytest.mark.asyncio
async def test_dispatch_scaffold_batch_refuses_track_without_app(
    bind_fresh_graph_context_for_async_tests,
):
    """Inside an open scaffold batch, create_track still needs app_id."""
    thread = await _thread("sess-track-app", 1, user_id="u1")
    propose = await dispatch_tool(
        "integral_propose_design",
        {"summary": "Car Rental", "proposal": _PROPOSAL, "blueprint": _BLUEPRINT},
        principal_id="u1",
        scope="ws1",
        session_id="sess-track-app",
    )
    assert not propose.is_error, propose
    await _approve_design("sess-track-app")

    m = await ChatMessage.create(role="user", thread_id=thread.id)
    await thread.connect(m, edge=CONTAINS)

    fresh_manual = await dispatch_tool(
        "integral_begin_batch",
        {"label": "Car Rental"},
        principal_id="u1",
        scope="ws1",
        session_id="sess-track-app",
    )
    assert fresh_manual.error_code == "use_approved_build_tool"

    opened = await dispatch_tool(
        "integral_begin_batch",
        {"label": "Car Rental", "manual_recovery": True},
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
async def test_artifact_upsert_get_list(bind_fresh_graph_context_for_async_tests):
    await _thread("sess-art", 1, user_id="u1")
    up = await dispatch_tool(
        "integral_upsert_artifact",
        {
            "key": "checklist",
            "kind": "checklist",
            "title": "Build checks",
            "body": "- [ ] tracks\n- [ ] views\n",
        },
        principal_id="u1",
        scope="ws1",
        session_id="sess-art",
    )
    assert not up.is_error, up
    assert up.data.get("version") == 1

    got = await dispatch_tool(
        "integral_get_artifact",
        {"key": "checklist"},
        principal_id="u1",
        scope="ws1",
        session_id="sess-art",
    )
    assert not got.is_error, got
    assert "tracks" in (got.data.get("body") or "")

    listed = await dispatch_tool(
        "integral_list_artifacts",
        {"kind": "checklist"},
        principal_id="u1",
        scope="ws1",
        session_id="sess-art",
    )
    assert not listed.is_error, listed
    assert listed.data.get("count") == 1
