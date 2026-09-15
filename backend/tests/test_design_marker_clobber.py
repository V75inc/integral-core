"""Regression: the design_proposed marker must survive an end-of-turn
assistant message append.

Root cause it guards against: ``record_design_proposed`` writes the marker on
a ChatThread instance it loads itself (via ``get_thread_by_session`` inside the
tool dispatch), while the chat turn loop holds a *separate*, older ChatThread
instance. jvspatial's row cache is not an identity map, so the two instances
are distinct objects. When the turn loop persists the assistant message it
saves its stale instance (``design_proposed`` still None) and clobbers the
marker the tool just wrote — so the next turn's commit_batch gate refuses a
legitimate build. ``append_message`` must refresh the marker before saving.
"""

from __future__ import annotations

import pytest

from app.models.edges import CONTAINS
from app.models.nodes import ChatMessage, ChatThread
from app.services import chat_threads


@pytest.mark.asyncio
async def test_assistant_append_does_not_clobber_design_marker():
    # Instance B — the turn loop's thread, loaded at turn start with no marker.
    thread = await ChatThread.create(user_id="u1", provider_session_id="sess-clobber")
    umsg = await ChatMessage.create(role="user", thread_id=thread.id)
    await thread.connect(umsg, edge=CONTAINS)

    # A tool records the marker mid-turn on a SEPARATE instance (instance A),
    # exactly as record_design_proposed does inside dispatch.
    result = await chat_threads.record_design_proposed(
        user_id="u1", session_id="sess-clobber", summary="Rentals app"
    )
    assert result.get("ok") is True

    # End-of-turn: the turn loop appends the assistant bubble using its OWN
    # stale instance B (design_proposed still None in memory).
    await chat_threads.append_message(
        thread=thread,
        role="assistant",
        parts=[{"type": "text", "text": "Staged the plan — approve to build."}],
    )

    # The marker must still be there for the next turn's commit_batch gate.
    reloaded = await ChatThread.get(thread.id)
    assert reloaded.design_proposed is not None
    assert reloaded.design_proposed["summary"] == "Rentals app"
