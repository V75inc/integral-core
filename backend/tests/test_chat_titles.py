"""Conversation title inference, failure isolation and rename fencing."""

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import chat_titles

pytestmark = [pytest.mark.asyncio, pytest.mark.smoke]


@pytest.fixture
def title_case(monkeypatch):
    user_text = "Can you help me plan a volunteer rota for our community clinic?"
    thread = SimpleNamespace(
        id="thread-1",
        title=chat_titles.derive_thread_title(user_text),
        title_source="fallback",
        updated_at="revision-1",
        nodes=AsyncMock(
            return_value=[
                SimpleNamespace(
                    role="user",
                    created_at="2026-10-11",
                    parts=[
                        {"type": "text", "text": user_text},
                        {"type": "tool-call", "args": {"secret": "exclude me"}},
                    ],
                )
            ]
        ),
    )
    get = AsyncMock(return_value=thread)
    monkeypatch.setattr(chat_titles.ChatThread, "get", get)
    model_run = AsyncMock(
        return_value=SimpleNamespace(output="Plan the community clinic volunteer rota")
    )
    calls = []

    def agent(model, **kwargs):
        calls.append((model, kwargs))
        return SimpleNamespace(run=model_run)

    monkeypatch.setattr(chat_titles, "Agent", agent)
    transaction = SimpleNamespace(
        find_one_and_update=AsyncMock(return_value={"id": thread.id})
    )

    @asynccontextmanager
    async def transact():
        yield transaction

    monkeypatch.setattr(
        "app.services.app_operations.transaction_scope.postgres_graph_transaction",
        transact,
    )
    return SimpleNamespace(
        thread=thread, get=get, run=model_run, calls=calls, tx=transaction
    )


async def test_infers_specific_title_from_public_conversation_text(title_case):
    model = object()
    await chat_titles.infer_conversation_title(
        thread_id="thread-1",
        model=model,
        assistant_text="Use weekly shifts with two volunteers per clinic session.",
    )
    assert title_case.calls[0][0] is model
    assert "untrusted data" in title_case.calls[0][1]["instructions"]
    transcript = json.loads(title_case.run.call_args.args[0])
    assert len(transcript) == 2
    assert "exclude me" not in json.dumps(transcript)
    update = title_case.tx.find_one_and_update.call_args
    assert update.args[1]["context.updated_at"] == "revision-1"
    assert (
        update.args[2]["$set"]["context.title"]
        == "Plan the community clinic volunteer rota"
    )
    assert update.args[2]["$set"]["context.title_source"] == "generated"


@pytest.mark.parametrize("source", ["manual", "generated"])
async def test_preserves_manual_and_already_generated_titles(title_case, source):
    title_case.thread.title_source = source
    await chat_titles.infer_conversation_title(
        thread_id="thread-1", model=object(), assistant_text="Answer"
    )
    title_case.run.assert_not_awaited()


async def test_concurrent_rename_wins(title_case):
    renamed = SimpleNamespace(
        title="My chosen name", title_source="manual", updated_at="revision-2"
    )
    title_case.get.side_effect = [title_case.thread, renamed]
    await chat_titles.infer_conversation_title(
        thread_id="thread-1", model=object(), assistant_text="Answer"
    )
    title_case.tx.find_one_and_update.assert_not_awaited()


async def test_failed_inference_keeps_fallback_and_can_retry(title_case):
    title_case.run.side_effect = TimeoutError()
    await chat_titles.infer_conversation_title(
        thread_id="thread-1", model=object(), assistant_text="Answer"
    )
    assert title_case.thread.title_source == "fallback"
    title_case.tx.find_one_and_update.assert_not_awaited()
    title_case.run.side_effect = None
    await chat_titles.infer_conversation_title(
        thread_id="thread-1", model=object(), assistant_text="Answer"
    )
    title_case.tx.find_one_and_update.assert_awaited_once()


@pytest.mark.parametrize(
    "output", ["", "x" * 161, "Truncated title…", "New chat", "```A title```"]
)
async def test_rejects_empty_or_truncated_model_output(title_case, output):
    title_case.run.return_value.output = output
    await chat_titles.infer_conversation_title(
        thread_id="thread-1", model=object(), assistant_text="Answer"
    )
    title_case.tx.find_one_and_update.assert_not_awaited()


async def test_legacy_custom_title_is_preserved(title_case):
    title_case.thread.title_source = ""
    title_case.thread.title = "My chosen title"
    await chat_titles.infer_conversation_title(
        thread_id="thread-1", model=object(), assistant_text="Answer"
    )
    title_case.run.assert_not_awaited()


async def test_actual_agent_uses_tool_free_title_prompt(title_case, monkeypatch):
    from pydantic_ai.models.function import FunctionModel

    from app.agentive.harness.pydantic_ai_compat import Agent, ModelResponse, TextPart

    def respond(messages, info):
        assert not info.function_tools
        assert "community clinic" in str(messages)
        return ModelResponse(
            parts=[
                TextPart(content="Coordinate weekly community clinic volunteer shifts")
            ]
        )

    monkeypatch.setattr(chat_titles, "Agent", Agent)
    await chat_titles.infer_conversation_title(
        thread_id="thread-1",
        model=FunctionModel(respond),
        assistant_text="Create weekly shifts.",
    )
    assert (
        title_case.tx.find_one_and_update.call_args.args[2]["$set"]["context.title"]
        == "Coordinate weekly community clinic volunteer shifts"
    )


async def test_durable_title_uses_authorized_transaction(title_case, monkeypatch):
    authorized_tx = SimpleNamespace(find_one_and_update=AsyncMock())
    context = object()

    @asynccontextmanager
    async def authorized(received):
        assert received is context
        yield SimpleNamespace(database=authorized_tx)

    monkeypatch.setattr(
        "app.agentive.services.work_items.authorized_work_item_effect", authorized
    )
    await chat_titles.infer_conversation_title(
        thread_id="thread-1",
        model=object(),
        assistant_text="Answer",
        work_execution_context=context,
    )
    authorized_tx.find_one_and_update.assert_awaited_once()
    title_case.tx.find_one_and_update.assert_not_awaited()


async def test_lost_durable_lease_prevents_title_write(title_case, monkeypatch):
    @asynccontextmanager
    async def unauthorized(context):
        raise RuntimeError("execution lease is no longer current")
        yield

    monkeypatch.setattr(
        "app.agentive.services.work_items.authorized_work_item_effect", unauthorized
    )
    await chat_titles.infer_conversation_title(
        thread_id="thread-1",
        model=object(),
        assistant_text="Answer",
        work_execution_context=object(),
    )
    title_case.tx.find_one_and_update.assert_not_awaited()


async def test_membership_revoked_during_inference_prevents_title_write(title_case):
    """A successful provider response does not authorize a stale title mutation."""
    from app.schemas.agentive.work import WorkError

    authority = AsyncMock(side_effect=WorkError("work.policy_denied"))
    with pytest.raises(WorkError, match="work.policy_denied"):
        await chat_titles.infer_conversation_title(
            thread_id="thread-1",
            model=object(),
            assistant_text="Answer",
            assert_authority=authority,
        )
    title_case.run.assert_awaited_once()
    title_case.tx.find_one_and_update.assert_not_awaited()
