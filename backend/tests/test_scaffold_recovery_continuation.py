"""Regression coverage for autonomous completion of affirmed scaffolds."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest

# Import api first to preserve the chat_streaming/api import-order guard.
import app.api  # noqa: F401
from app.agentive import staging
from app.agentive.staging import append_to_batch, open_batch
from app.api import ai_chat


class _Thread:
    id = "n.ChatThread.scaffold-recovery"
    provider_session_id = "sess-scaffold-recovery"


@pytest.fixture(autouse=True)
def _clean_staging():
    staging._reset_for_tests()
    yield
    staging._reset_for_tests()


@pytest.mark.asyncio
async def test_affirmed_open_scaffold_schedules_one_recovery_with_refs(monkeypatch):
    """A partial affirmed build continues without another user message."""
    await open_batch(user_id="u1", session_id=_Thread.provider_session_id)
    await append_to_batch(
        user_id="u1",
        session_id=_Thread.provider_session_id,
        op={
            "kind": "create_app",
            "summary": "Create rental app",
            "diff_human": "…",
            "diff_machine": {},
            "payload": {"name": "Car Rental Management"},
        },
    )

    async def _affirmed(_session_id: str) -> bool:
        return True

    calls: List[Dict[str, Any]] = []

    async def _fake_run(**kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(ai_chat.chat_store, "design_chat_affirmed_for_build", _affirmed)
    monkeypatch.setattr(ai_chat, "_run_scaffold_continuation_turn", _fake_run)

    started = await ai_chat._schedule_scaffold_continuation(
        status="succeeded",
        user_id="u1",
        thread=_Thread(),
        workspace_id="n.Workspace.w1",
    )
    assert started is True
    # The task is intentionally detached from the finished user SSE stream.
    await asyncio.sleep(0)
    assert len(calls) == 1
    prompt = calls[0]["prompt"]
    assert "CONTINUE-AFFIRMED-SCAFFOLD" in prompt
    assert 'app_id="{{app.id}}"' in prompt
    assert "Do not ask the user a question" in prompt

    # A duplicate completed notification sees the outstanding claim and does
    # not create a competing turn for this batch.
    assert (
        await ai_chat._schedule_scaffold_continuation(
            status="succeeded",
            user_id="u1",
            thread=_Thread(),
            workspace_id="n.Workspace.w1",
        )
        is False
    )


@pytest.mark.asyncio
async def test_failed_turn_never_schedules_scaffold_recovery(monkeypatch):
    """A provider failure remains visible; it does not spend autonomous work."""

    async def _should_not_be_called(_session_id: str) -> bool:
        raise AssertionError("failed turns must short-circuit before eligibility")

    monkeypatch.setattr(
        ai_chat.chat_store, "design_chat_affirmed_for_build", _should_not_be_called
    )
    assert (
        await ai_chat._schedule_scaffold_continuation(
            status="failed",
            user_id="u1",
            thread=_Thread(),
            workspace_id=None,
        )
        is False
    )


def test_recovery_uses_commit_receipt_not_model_prose_for_outcome():
    """A headless retry only reports completion after batch_applied."""
    events = [
        {
            "type": "tool-call",
            "name": "integral_commit_batch",
            "status": "complete",
            "result": {"_kind": "batch_incomplete"},
        },
        {"type": "text-delta", "delta": "I do not have confirmation."},
    ]
    assert ai_chat._scaffold_recovery_commit_outcome(events) == "batch_incomplete"

    events.append(
        {
            "type": "tool-call",
            "name": "integral_commit_batch",
            "status": "complete",
            "result": '{"_kind":"batch_applied","applied":true}',
        }
    )
    assert ai_chat._scaffold_recovery_commit_outcome(events) == "batch_applied"


@pytest.mark.asyncio
async def test_recovery_status_only_persists_receipt_backed_terminal_text(monkeypatch):
    """Intermediate recovery prose is never rendered as the build verdict."""
    calls: List[Dict[str, Any]] = []

    async def _append(**kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(ai_chat.chat_store, "append_message", _append)
    thread = _Thread()

    await ai_chat._append_scaffold_recovery_status(
        thread=thread, outcome="batch_incomplete", exhausted=False
    )
    assert calls == []

    await ai_chat._append_scaffold_recovery_status(
        thread=thread, outcome="batch_applied", exhausted=False
    )
    assert len(calls) == 1
    assert "build is complete" in calls[0]["parts"][0]["text"]
    assert calls[0]["provider_metadata"]["outcome"] == "batch_applied"
