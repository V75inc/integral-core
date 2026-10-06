"""Approval uses the native Agent and exact proposal/message authority."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from pydantic_ai import CancellationToken
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from app.agentive.harness import design_approval
from app.agentive.harness.contracts import HarnessExecutionScope
from app.api.errors import InsufficientPermissionsError, ResourceConflictError


def scope():
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
@pytest.mark.parametrize("verdict", ["approve", "amend", "decline", "other"])
@pytest.mark.parametrize("cas_success", [True, False])
async def test_native_verdict_is_bound_to_current_proposal_and_message(
    monkeypatch, verdict, cas_success
):
    utterance = "That sounds good. Go ahead."
    thread = SimpleNamespace(
        user_id="user-a",
        workspace_id="workspace-a",
        provider_id="integral_native",
        updated_at="revision-a",
        last_message_at="message-a",
        active_harness_session_id="n.HarnessSession.session-node-a",
        design_proposed={
            "design_id": "design-a",
            "proposal": "One equipment register.",
            "proposed_at": "proposal-a",
            "proposed_at_user_turn": 1,
            "blueprint_digest": "digest-a",
            "blueprint_revision": 1,
        },
    )
    writes = []
    requests = []

    async def get_thread(_id):
        return thread

    async def get_session(_id):
        assert _id == thread.active_harness_session_id
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
            return {"id": "thread-a"} if cas_success else None

    @asynccontextmanager
    async def transaction(_work):
        yield Transaction()

    def respond(messages, info):
        requests.append(messages)
        assert not info.function_tools
        return ModelResponse(
            parts=[ToolCallPart(info.output_tools[0].name, {"response": verdict})]
        )

    monkeypatch.setattr(design_approval.ChatThread, "get", get_thread)
    monkeypatch.setattr(design_approval.HarnessSession, "get", get_session)
    monkeypatch.setattr(design_approval.chat_threads, "count_user_turns", count)
    monkeypatch.setattr(
        design_approval.chat_threads, "latest_user_message_text", latest
    )
    monkeypatch.setattr(design_approval, "_authority_transaction", transaction)
    if not cas_success:
        with pytest.raises(ResourceConflictError, match="changed during approval"):
            await design_approval.resolve_pending_design_reply(
                scope=scope(),
                utterance=utterance,
                model=FunctionModel(respond),
                cancellation=CancellationToken(),
            )
        return
    result = await design_approval.resolve_pending_design_reply(
        scope=scope(),
        utterance=utterance,
        model=FunctionModel(respond),
        cancellation=CancellationToken(),
    )
    assert result is (verdict == "approve")
    assert len(requests) == 1
    fence, _ = writes[0]
    assert fence["context.last_run_id"] == "run-a"
    assert fence["context.session_id"] == "session-a"
    predicate, change = writes[1]
    assert predicate["context.design_proposed.design_id"] == "design-a"
    assert predicate["context.design_proposed.blueprint_digest"] == "digest-a"
    assert predicate["context.design_proposed.blueprint_revision"] == 1
    assert predicate["context.last_message_at"] == "message-a"
    assert (
        predicate["context.active_harness_session_id"]
        == thread.active_harness_session_id
    )
    stamped = change["$set"]["context.design_proposed"]
    assert stamped["approved"] is result
    assert stamped["affirm_for"] == utterance
    assert stamped["affirm_run_id"] == "run-a"
    assert stamped["reply_kind"] == verdict


@pytest.mark.asyncio
async def test_foreign_thread_cannot_be_judged_or_approved(monkeypatch):
    async def foreign(_id):
        return SimpleNamespace(
            user_id="another-user",
            workspace_id="workspace-a",
            provider_id="integral_native",
        )

    monkeypatch.setattr(design_approval.ChatThread, "get", foreign)
    with pytest.raises(InsufficientPermissionsError):
        await design_approval.resolve_pending_design_reply(
            scope=scope(),
            utterance="yes",
            model=None,
            cancellation=CancellationToken(),
        )
