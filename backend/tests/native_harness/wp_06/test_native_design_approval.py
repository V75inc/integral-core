"""The primary tool choice grants exact pending-design approval."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.agentive.harness import design_approval
from app.agentive.harness.contracts import HarnessExecutionScope
from app.api.errors import InsufficientPermissionsError


def scope():
    """Return a representative, isolated native execution scope."""
    return HarnessExecutionScope(
        tenant_id="workspace-a",
        workspace_id="workspace-a",
        principal_id="user-a",
        thread_id="thread-a",
        session_id="session-a",
        run_id="run-a",
        permission_revision="permissions-a",
        capability_version="capabilities-a",
    )


@pytest.mark.asyncio
async def test_build_tool_selection_persists_scoped_approval(monkeypatch):
    """Primary tool selection persists approval against the live run fence."""
    utterance = "That looks good. Please build it and add the drill now."
    thread = SimpleNamespace(
        user_id="user-a",
        workspace_id="workspace-a",
        provider_id="integral_native",
        updated_at="revision-a",
        last_message_at="message-a",
        active_harness_session_id="n.HarnessSession.session-node-a",
        design_proposed={
            "design_id": "design-a",
            "proposed_at": "proposal-a",
            "proposed_at_user_turn": 1,
            "blueprint_digest": "digest-a",
            "blueprint_revision": 1,
        },
    )
    writes = []

    async def get_thread(_id):
        return thread

    async def get_session(_id):
        return SimpleNamespace(
            id=thread.active_harness_session_id,
            session_id="session-a",
            thread_id="thread-a",
            principal_id="user-a",
            workspace_id="workspace-a",
            last_run_id="run-a",
            status="active",
        )

    async def count(_thread):
        return 2

    async def latest(_thread):
        return utterance

    class Transaction:
        async def find_one_and_update(self, collection, predicate, change):
            writes.append((predicate, change))
            if predicate["id"] == thread.active_harness_session_id:
                return {"id": thread.active_harness_session_id}
            return {"id": "thread-a"}

    @asynccontextmanager
    async def transaction(_work):
        yield Transaction()

    monkeypatch.setattr(design_approval.ChatThread, "get", get_thread)
    monkeypatch.setattr(design_approval.HarnessSession, "get", get_session)
    monkeypatch.setattr(design_approval.chat_threads, "count_user_turns", count)
    monkeypatch.setattr(
        design_approval.chat_threads, "latest_user_message_text", latest
    )
    monkeypatch.setattr(design_approval, "_authority_transaction", transaction)

    assert await design_approval.authorize_pending_design_build(
        scope=scope(), expected_user_turn=2, expected_utterance=utterance
    )
    assert len(writes) == 2
    fence, _ = writes[0]
    assert fence["context.last_run_id"] == "run-a"
    predicate, change = writes[1]
    assert predicate["context.design_proposed.design_id"] == "design-a"
    assert predicate["context.design_proposed.blueprint_digest"] == "digest-a"
    assert predicate["context.design_proposed.blueprint_revision"] == 1
    approved = change["$set"]["context.design_proposed"]
    assert approved["approved"] is True
    assert approved["affirm_for"] == utterance
    assert approved["affirm_via"] == "native_build_tool_selection"
    assert approved["approved_via"] == "native_build_tool_selection"


@pytest.mark.asyncio
async def test_preflight_does_not_run_a_second_approval_judge(monkeypatch):
    """Approval preflight reads durable state without classifying user text."""
    thread = SimpleNamespace(
        user_id="user-a",
        workspace_id="workspace-a",
        provider_id="integral_native",
        design_proposed={
            "design_id": "design-a",
            "proposed_at_user_turn": 1,
            "approved": False,
        },
    )

    async def get_thread(_id):
        return thread

    async def count(_thread):
        return 2

    async def latest(_thread):
        return "That looks good. Please build it."

    monkeypatch.setattr(design_approval.ChatThread, "get", get_thread)
    monkeypatch.setattr(design_approval.chat_threads, "count_user_turns", count)
    monkeypatch.setattr(
        design_approval.chat_threads, "latest_user_message_text", latest
    )
    assert not await design_approval.pending_design_is_approved_for_reply(
        scope=scope(), utterance="That looks good. Please build it."
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "approved_run,expected", [("older-run", False), ("run-a", True)]
)
async def test_previous_approval_does_not_freeze_later_repair(
    monkeypatch, approved_run, expected
):
    thread = SimpleNamespace(
        user_id="user-a",
        workspace_id="workspace-a",
        provider_id="integral_native",
        design_proposed={
            "design_id": "design-a",
            "proposed_at_user_turn": 1,
            "approved": True,
            "affirm_run_id": approved_run,
        },
    )

    async def get(_id):
        return thread

    async def count(_thread):
        return 2

    async def latest(_thread):
        return "Please revise the missing sample statuses."

    monkeypatch.setattr(design_approval.ChatThread, "get", get)
    monkeypatch.setattr(design_approval.chat_threads, "count_user_turns", count)
    monkeypatch.setattr(
        design_approval.chat_threads, "latest_user_message_text", latest
    )
    assert (
        await design_approval.pending_design_is_approved_for_reply(
            scope=scope(), utterance=await latest(thread)
        )
        is expected
    )


@pytest.mark.asyncio
async def test_foreign_thread_cannot_receive_build_authority(monkeypatch):
    """A thread owned by another principal cannot grant build authority."""

    async def foreign(_id):
        return SimpleNamespace(
            user_id="another-user",
            workspace_id="workspace-a",
            provider_id="integral_native",
        )

    monkeypatch.setattr(design_approval.ChatThread, "get", foreign)
    with pytest.raises(InsufficientPermissionsError):
        await design_approval.authorize_pending_design_build(
            scope=scope(), expected_user_turn=2, expected_utterance="Go ahead"
        )
