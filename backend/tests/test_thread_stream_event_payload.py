"""The stream-update payload has to carry enough to act on.

Events fan out to every socket a user has open, regardless of which workspace
that tab is showing. Without `workspace_id` a tab cannot tell whether a busy
thread belongs to what is on screen; without `turn_id` a fast
completed-then-started pair is indistinguishable, so "is this thread busy?"
becomes unanswerable exactly when it matters.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

import app.api  # noqa: F401  (import-order guard; see chat_streaming tests)
from app.services import chat_thread_events


@pytest.mark.asyncio
async def test_payload_carries_workspace_and_turn(monkeypatch):
    pushed: List[Dict[str, Any]] = []

    async def _capture(user_id, event, payload):
        pushed.append({"user_id": user_id, "event": event, "payload": payload})

    monkeypatch.setattr(chat_thread_events, "push_agent_event", _capture)

    await chat_thread_events.notify_thread_stream_update(
        "u1",
        "n.ChatThread.1",
        status="started",
        workspace_id="n.Workspace.1",
        turn_id="turn-abc",
        extra={"origin": "routine_task"},
    )

    assert len(pushed) == 1
    payload = pushed[0]["payload"]
    assert payload["thread_id"] == "n.ChatThread.1"
    assert payload["status"] == "started"
    assert payload["workspace_id"] == "n.Workspace.1"
    assert payload["turn_id"] == "turn-abc"
    # `extra` still merges — the origin badge depends on it.
    assert payload["origin"] == "routine_task"


@pytest.mark.asyncio
async def test_absent_fields_are_omitted_not_null(monkeypatch):
    """A caller without a workspace must not emit `workspace_id: None`.

    The client treats presence as meaningful; a null would read as a real
    value and could match a tab whose own scope is unresolved.
    """
    pushed: List[Dict[str, Any]] = []

    async def _capture(user_id, event, payload):
        pushed.append(payload)

    monkeypatch.setattr(chat_thread_events, "push_agent_event", _capture)

    await chat_thread_events.notify_thread_stream_update(
        "u1", "n.ChatThread.1", status="completed"
    )

    payload = pushed[0]
    assert "workspace_id" not in payload
    assert "turn_id" not in payload
