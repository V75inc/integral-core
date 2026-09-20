"""Prompt Sheet queue — enqueue, gate, drain, cancel."""

from __future__ import annotations

import pytest

from app.models.nodes import ChatThread
from app.services import chat_threads
from app.services import prompt_queue as pq


async def _blessed(token):
    """Stand-in for a genuinely blessed StagedChange.

    ``mark_write_item`` now verifies the token really was blessed before
    recording "approved" — the sheet used to take the caller's word for it and
    tell the model a write had landed while the card sat pending.
    """

    class _SC:
        state = "consumed"

    return _SC()


@pytest.mark.asyncio
async def test_enqueue_question_opens_queue_and_blocks_tools(monkeypatch):
    """Enqueue opens the queue so the tool gate sees it as blocking."""
    thread = await ChatThread.create(
        user_id="u-pq-1",
        workspace_id="ws-1",
        provider_id="jvagent",
        provider_session_id="sess-pq-1",
        title="t",
    )

    async def _by_session(sid):
        if sid == "sess-pq-1":
            return await ChatThread.get(thread.id)
        return None

    monkeypatch.setattr(chat_threads, "get_thread_by_session", _by_session)
    monkeypatch.setattr(pq, "get_thread_by_session", _by_session)
    monkeypatch.setattr("app.agentive.staging.get_token", _blessed)

    asked = await pq.enqueue_questions(
        user_id="u-pq-1",
        session_id="sess-pq-1",
        questions=[
            {
                "question": "Which mode?",
                "options": [{"label": "A"}, {"label": "B"}],
            }
        ],
    )
    assert asked.get("_kind") == "user_question"
    assert await pq.session_queue_is_open("sess-pq-1")
    # Gate is session_queue_is_open — dispatch calls the same helper.
    assert pq.queue_is_open(await ChatThread.get(thread.id))


@pytest.mark.asyncio
async def test_drain_resume_and_cancel_all(monkeypatch):
    """Resolving the last item closes the queue and yields a resume summary."""
    thread = await ChatThread.create(
        user_id="u-pq-2",
        workspace_id="ws-1",
        provider_id="jvagent",
        provider_session_id="sess-pq-2",
        title="t",
    )

    async def _by_session(sid):
        if sid == "sess-pq-2":
            return await ChatThread.get(thread.id)
        return None

    monkeypatch.setattr(chat_threads, "get_thread_by_session", _by_session)
    monkeypatch.setattr(pq, "get_thread_by_session", _by_session)
    monkeypatch.setattr("app.agentive.staging.get_token", _blessed)

    await pq.enqueue_questions(
        user_id="u-pq-2",
        session_id="sess-pq-2",
        questions=[
            {
                "question": "Q1?",
                "options": [{"label": "Yes"}, {"label": "No"}],
            },
            {
                "question": "Q2?",
                "options": [{"label": "X"}, {"label": "Y"}],
            },
        ],
    )
    fresh = await ChatThread.get(thread.id)
    items = pq.get_queue(fresh)["items"]
    assert len(items) == 2

    r1 = await pq.resolve_question_item(
        user_id="u-pq-2",
        thread=fresh,
        item_id=items[0]["id"],
        choices=["Yes"],
    )
    assert r1.get("closed") is not True

    fresh = await ChatThread.get(thread.id)
    r2 = await pq.resolve_question_item(
        user_id="u-pq-2",
        thread=fresh,
        item_id=items[1]["id"],
        skip=True,
    )
    assert r2.get("closed") is True
    assert "skipped" in (r2.get("resume_text") or "").lower()
    assert "[PROMPT_SHEET]" in (r2.get("resume_text") or "")
    assert not await pq.session_queue_is_open("sess-pq-2")


@pytest.mark.asyncio
async def test_cancel_all_keeps_approved(monkeypatch):
    """Cancel all clears pending items but leaves already-approved writes."""
    thread = await ChatThread.create(
        user_id="u-pq-3",
        workspace_id="ws-1",
        provider_id="jvagent",
        provider_session_id="sess-pq-3",
        title="t",
    )

    async def _by_session(sid):
        if sid == "sess-pq-3":
            return await ChatThread.get(thread.id)
        return None

    monkeypatch.setattr(chat_threads, "get_thread_by_session", _by_session)
    monkeypatch.setattr(pq, "get_thread_by_session", _by_session)
    monkeypatch.setattr("app.agentive.staging.get_token", _blessed)

    await pq.enqueue_questions(
        user_id="u-pq-3",
        session_id="sess-pq-3",
        questions=[
            {
                "question": "Stay?",
                "options": [{"label": "A"}, {"label": "B"}],
            }
        ],
    )
    await pq.enqueue_staged_write(
        user_id="u-pq-3",
        session_id="sess-pq-3",
        staged={
            "token": "tok-keep",
            "kind": "create_entry",
            "summary": "already approved in sheet",
            "state": "pending",
            "diff_human": "h",
            "diff_machine": {},
        },
    )
    fresh = await ChatThread.get(thread.id)
    # Mark write approved (simulates bless mid-sheet).
    await pq.mark_write_item(
        user_id="u-pq-3",
        thread=fresh,
        token="tok-keep",
        status="approved",
    )
    fresh = await ChatThread.get(thread.id)
    # Re-open a pending question so cancel has something to clear.
    await pq.enqueue_questions(
        user_id="u-pq-3",
        session_id="sess-pq-3",
        questions=[
            {
                "question": "More?",
                "options": [{"label": "C"}, {"label": "D"}],
            }
        ],
    )
    # Wait - after mark_write with only question pending + approved write,
    # if question was still pending cancel works. First question still pending.
    fresh = await ChatThread.get(thread.id)
    # Actually after first enqueue we had 1 question pending; then staged write;
    # then mark write approved; questions still pending so queue still open.
    # Then we tried enqueue again - GATE would block in dispatch but enqueue_questions
    # directly still appends. Fine for unit test.
    cancelled = await pq.cancel_all(user_id="u-pq-3", thread=fresh)
    assert cancelled.get("closed") is True
    q = cancelled["queue"]
    approved = [i for i in q["items"] if i.get("token") == "tok-keep"]
    assert approved and approved[0]["status"] == "approved"
    pending_left = [i for i in q["items"] if i.get("status") == "pending"]
    assert not pending_left


@pytest.mark.asyncio
async def test_reopen_does_not_stack_prior_resolved(monkeypatch):
    """Closed-sheet items must not reappear when the next write/question opens."""
    thread = await ChatThread.create(
        user_id="u-pq-4",
        workspace_id="ws-1",
        provider_id="jvagent",
        provider_session_id="sess-pq-4",
        title="t",
    )

    async def _by_session(sid):
        if sid == "sess-pq-4":
            return await ChatThread.get(thread.id)
        return None

    monkeypatch.setattr(chat_threads, "get_thread_by_session", _by_session)
    monkeypatch.setattr(pq, "get_thread_by_session", _by_session)
    monkeypatch.setattr("app.agentive.staging.get_token", _blessed)

    await pq.enqueue_staged_write(
        user_id="u-pq-4",
        session_id="sess-pq-4",
        staged={
            "token": "tok-old-1",
            "kind": "file_content",
            "summary": "paid early",
            "state": "pending",
        },
    )
    await pq.enqueue_staged_write(
        user_id="u-pq-4",
        session_id="sess-pq-4",
        staged={
            "token": "tok-old-2",
            "kind": "delete_entry",
            "summary": "delete early",
            "state": "pending",
        },
    )
    fresh = await ChatThread.get(thread.id)
    await pq.mark_write_item(
        user_id="u-pq-4", thread=fresh, token="tok-old-1", status="approved"
    )
    fresh = await ChatThread.get(thread.id)
    drained = await pq.mark_write_item(
        user_id="u-pq-4", thread=fresh, token="tok-old-2", status="approved"
    )
    assert drained.get("closed") is True
    assert "paid early" in (drained.get("resume_text") or "")

    # Next sheet episode — only the new write.
    await pq.enqueue_staged_write(
        user_id="u-pq-4",
        session_id="sess-pq-4",
        staged={
            "token": "tok-new",
            "kind": "file_content",
            "summary": "paid late",
            "state": "pending",
        },
    )
    q = pq.get_queue(await ChatThread.get(thread.id))
    assert q["status"] == "open"
    assert len(q["items"]) == 1
    assert q["items"][0]["token"] == "tok-new"
    assert q["items"][0]["summary"] == "paid late"


def test_resume_after_approved_write_requires_readback_before_new_mutation():
    """An approval resume must not invite the model to stage the same write."""
    resume = pq.build_resume_summary(
        {
            "close_reason": "drained",
            "items": [
                {
                    "kind": pq.ITEM_STAGED_WRITE,
                    "status": pq.STATUS_APPROVED,
                    "write_kind": "create_dashboard",
                    "summary": "Create Fleet Overview",
                }
            ],
        }
    )

    assert "Approved — Create Fleet Overview" in resume
    assert "already been applied" in resume
    assert "Do not repeat, re-stage, or cancel" in resume
    assert "First read back" in resume


def test_resume_after_profile_revision_requires_diff_and_publish():
    """A profile revision approval modifies only a draft, never the live schema."""
    resume = pq.build_resume_summary(
        {
            "close_reason": "drained",
            "items": [
                {
                    "kind": pq.ITEM_STAGED_WRITE,
                    "status": pq.STATUS_APPROVED,
                    "write_kind": "propose_profile_revision",
                    "summary": "Apply Inspector field to the Inspections profile",
                    "diff_machine": {"draft_id": "draft-inspections"},
                }
            ],
        }
    )

    assert "unpublished draft (draft-inspections)" in resume
    assert "integral_diff_profile_draft" in resume
    assert "integral_publish_profile_draft" in resume
    assert "Do not claim the schema is live" in resume
