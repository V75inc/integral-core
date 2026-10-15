"""Conversational mode cannot confer workspace or write authority."""

from types import SimpleNamespace

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from app.agentive.harness.adaptive import (
    EscalatedTurn,
    LightTurnResult,
    OperationalContextRequest,
    build_light_agent,
    public_dialogue,
    requires_operational_path,
)
from app.api.errors import InsufficientPermissionsError
from app.services.chat_providers.base import ChatTurnContext
from app.services.chat_providers.pydantic_ai_provider import PydanticAIProvider


def context(text="hello there", **extra):
    return SimpleNamespace(text=text, extra_data=extra)


@pytest.mark.parametrize(
    "extra",
    [
        {"pending_approval_tokens": {"change": "token"}},
        {"pending_approvals": [{"id": "proposal"}]},
        {"staging_outcome_continuation": True},
        {"attachment_only_input": True},
        {"entities_referenced": [{"id": "entry"}]},
        {"design_only": True},
        {"origin": "background_task"},
    ],
)
def test_host_operations_enter_existing_governed_path(extra):
    assert requires_operational_path(context(**extra), SimpleNamespace(), None)


@pytest.mark.parametrize(
    "text",
    ["hello", "thanks", "yes", "no", "show records", "hi, rename this app", "bonjour"],
)
def test_text_does_not_supply_permission_or_classify_host_authority(text):
    assert not requires_operational_path(context(text), SimpleNamespace(), None)


@pytest.mark.parametrize("status", ["failed", "running", "cancelled"])
def test_unsettled_previous_operational_run_never_uses_light_path(status):
    assert requires_operational_path(
        context(), SimpleNamespace(), SimpleNamespace(status=status)
    )


def test_pending_design_and_empty_host_turn_enter_heavy_path():
    assert requires_operational_path(
        context(), SimpleNamespace(design_proposed={"blueprint": {"apps": []}}), None
    )
    assert requires_operational_path(context(""), SimpleNamespace(), None)


@pytest.mark.asyncio
async def test_one_model_pass_can_answer_without_workspace_tools():
    calls = []

    def answer(messages, info):
        calls.append(info)
        assert info.function_tools == []
        assert [t.name for t in info.output_tools] == [
            "request_operational_context",
            "propose_simple_reminder",
        ]
        return ModelResponse(parts=[TextPart("Hello! What can I help with?")])

    result = await build_light_agent(FunctionModel(answer)).run("hello there")
    assert result.output == "Hello! What can I help with?"
    assert len(calls) == result.usage.requests == 1


@pytest.mark.asyncio
async def test_light_pass_receives_verified_clock_without_lookup_tools():
    def answer(messages, info):
        assert "Trusted clock: 2044-01-01 America/Guyana" in str(messages)
        assert not info.function_tools
        return ModelResponse(parts=[TextPart("1 January 2044.")])

    result = await build_light_agent(
        FunctionModel(answer), time_context="Trusted clock: 2044-01-01 America/Guyana"
    ).run("What is today's date?")
    assert result.output == "1 January 2044."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "show my expenses",
        "what is on this page?",
        "hi, rename this app",
        "yes",
        "montrer mes dépenses",
    ],
)
async def test_escalation_is_typed_output_without_tool_effect(text):
    result = await build_light_agent(
        FunctionModel(
            lambda m, i: ModelResponse(
                parts=[
                    ToolCallPart(
                        "request_operational_context",
                        {},
                        "escalate",
                    )
                ]
            )
        )
    ).run(text)
    assert isinstance(result.output, OperationalContextRequest)
    assert result.usage.requests == 1


def test_public_projection_excludes_host_reasoning_tools_and_current_input():
    def message(id, role, parts):
        return SimpleNamespace(id=id, role=role, parts=parts)

    projected = public_dialogue(
        [
            message("host", "system", [{"type": "text", "text": "PRIVATE"}]),
            message("user", "user", [{"type": "text", "text": "hello"}]),
            message(
                "assistant",
                "assistant",
                [
                    {"type": "reasoning", "text": "PRIVATE"},
                    {"type": "tool-call", "result": "PRIVATE"},
                    {"type": "text", "text": "Hi!"},
                ],
            ),
            message("current", "user", [{"type": "text", "text": "thanks"}]),
        ],
        exclude_id="current",
    )
    assert len(projected) == 2
    assert "PRIVATE" not in str(projected)
    assert "thanks" not in str(projected)
    large = [
        message(str(i), "user", [{"type": "text", "text": "a" * 2000}])
        for i in range(30)
    ]
    assert len(public_dialogue(large)) == 6


async def setup_turn(monkeypatch, *, escalation=False, origin="http"):
    from app.agentive.services.execution_runs import start_run
    from app.services.chat_threads import append_message, create_thread
    from tests.test_access_perimeter_regressions import rooted_graph

    owner, _, workspace, _, _, _ = await rooted_graph()
    thread = await create_thread(
        user_id=owner.id, workspace_id=workspace.id, provider_id="integral_native"
    )
    message = await append_message(
        thread=thread, role="user", parts=[{"type": "text", "text": "hello there"}]
    )
    run = await start_run(
        thread_id=thread.id,
        user_id=owner.id,
        workspace_id=workspace.id,
        provider_id="integral_native",
        origin=origin,
        metadata={"chat_message_id": message.id},
    )
    calls = []

    def respond(messages, info):
        calls.append(info)
        return (
            ModelResponse(
                parts=[
                    ToolCallPart(
                        "request_operational_context",
                        {"reason": "needs current scoped context " * 20},
                        "e",
                    )
                ]
            )
            if escalation
            else ModelResponse(parts=[TextPart("Hello!")])
        )

    async def route(**kwargs):
        return SimpleNamespace()

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "a" * 32)
    monkeypatch.setattr(
        "app.services.chat_providers.pydantic_ai_provider.resolve_native_model_route",
        route,
    )
    monkeypatch.setattr(
        "app.services.chat_providers.pydantic_ai_provider.build_litellm_sdk_model",
        lambda **kwargs: FunctionModel(respond),
    )
    ctx = ChatTurnContext(
        user_id=owner.id,
        user_email="adaptive@example.test",
        workspace_id=workspace.id,
        thread_id=thread.id,
        session_id=None,
        text="hello there",
        extra_data={"run_id": run.run_id},
    )
    return ctx, thread, run, calls


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["http", "chat"])
async def test_provider_light_preparation_skips_skills_and_preserves_checkpoint(
    monkeypatch,
    origin,
):
    ctx, thread, _, calls = await setup_turn(monkeypatch, origin=origin)

    def forbidden(*args, **kwargs):
        raise AssertionError("light mode prepared operational skills")

    monkeypatch.setattr(
        "app.agentive.workspace_agent_profile.compose_workspace_agent_profile",
        forbidden,
    )
    result = await PydanticAIProvider()._prepare(ctx)
    assert isinstance(result, LightTurnResult) and result.text == "Hello!"
    assert len(calls) == 1
    from app.models.nodes import ChatThread

    persisted = await ChatThread.get(thread.id)
    assert persisted.active_harness_session_id is None
    assert persisted.provider_session_id is None


@pytest.mark.asyncio
async def test_provider_escalation_keeps_usage_in_same_run(monkeypatch):
    ctx, thread, _, calls = await setup_turn(monkeypatch, escalation=True)
    result = await PydanticAIProvider()._try_light_turn(ctx, thread, "owner", None)
    assert isinstance(result, EscalatedTurn)
    assert result.input_tokens > 0 and len(calls) == 1


@pytest.mark.asyncio
async def test_light_turn_rejects_cross_principal_before_inference(monkeypatch):
    from dataclasses import replace

    ctx, _, _, calls = await setup_turn(monkeypatch)
    with pytest.raises(InsufficientPermissionsError):
        await PydanticAIProvider()._prepare(replace(ctx, user_id="outsider"))
    assert calls == []


@pytest.mark.asyncio
async def test_light_turn_preserves_existing_operational_checkpoint(monkeypatch):
    from app.models.nodes import ChatThread, HarnessSession

    ctx, thread, _, _ = await setup_turn(monkeypatch)
    session = await HarnessSession.create(
        session_id="previous-operational-session",
        thread_id=thread.id,
        principal_id=ctx.user_id,
        workspace_id=ctx.workspace_id,
        last_checkpoint_id="previous-checkpoint",
        last_checkpoint_run_id="previous-operational-run",
    )
    thread.active_harness_session_id = session.id
    thread.provider_session_id = session.session_id
    await thread.save()
    result = await PydanticAIProvider()._prepare(ctx)
    assert isinstance(result, LightTurnResult)
    after = await HarnessSession.get(session.id)
    persisted = await ChatThread.get(thread.id)
    assert after.last_checkpoint_id == "previous-checkpoint"
    assert after.last_checkpoint_run_id == "previous-operational-run"
    assert persisted.active_harness_session_id == session.id
    assert persisted.provider_session_id == session.session_id


@pytest.mark.asyncio
async def test_lookup_cache_cannot_expose_uncontained_message(monkeypatch):
    from app.agentive.harness.adaptive import recent_public_messages
    from app.models.nodes import ChatMessage

    ctx, thread, _, _ = await setup_turn(monkeypatch)
    forged = await ChatMessage.create(
        thread_id=thread.id,
        role="assistant",
        parts=[{"type": "text", "text": "FORGED_UNCONTAINED"}],
        created_at="2099-01-01T00:00:00Z",
    )
    messages = await recent_public_messages(thread)
    assert forged.id not in {m.id for m in messages}
    assert "FORGED_UNCONTAINED" not in str(public_dialogue(messages))


@pytest.mark.asyncio
async def test_light_answer_retains_private_envelope_normalization(monkeypatch):
    ctx, _, _, _ = await setup_turn(monkeypatch)
    monkeypatch.setattr(
        "app.services.chat_providers.pydantic_ai_provider.build_litellm_sdk_model",
        lambda **kwargs: FunctionModel(
            lambda m, i: ModelResponse(
                parts=[
                    TextPart(
                        '{"thought":"PRIVATE_REASONING", "role":"assistant", "content":"Hello!"}'
                    )
                ]
            )
        ),
    )
    result = await PydanticAIProvider()._prepare(ctx)
    assert result.text == "Hello!"
    assert "PRIVATE_REASONING" not in result.text


@pytest.mark.asyncio
async def test_full_light_stream_finishes_without_session_update_or_title_model(
    monkeypatch,
):
    ctx, _, _, calls = await setup_turn(monkeypatch)
    events = [e async for e in PydanticAIProvider().stream_turn(ctx)]
    assert [e["type"] for e in events] == ["text-delta", "message-finish"]
    assert events[-1]["contextMode"] == "conversation"
    assert len(calls) == 1
    # Exercise the actual producer through the durable boundary. The light
    # completion timing previously caused every successful answer to fail here.
    from app.agentive.services.work_worker import _native_chat_transcript
    from app.services.chat_turn_events import normalize_public_event
    from app.services.chat_turn_transcript import _public_metadata

    replay = [normalize_public_event(event) for event in events]
    assert replay == events
    parts, metadata = _native_chat_transcript(replay)
    assert parts == [{"type": "text", "text": "Hello!"}]
    assert metadata["timing"]["modelPassMs"] >= 0
    assert _public_metadata(metadata)["timing"] == metadata["timing"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,origin,parent",
    [
        ("routine", "schedule", ""),
        ("chat_turn", "mandate_review", ""),
        ("chat_turn", "interactive_chat", "parent-work"),
    ],
)
async def test_background_and_child_work_cannot_take_conversational_shortcut(
    monkeypatch, kind, origin, parent
):
    from app.agentive.work_models import WorkItem

    ctx, thread, run, calls = await setup_turn(monkeypatch)
    item = await WorkItem.create(
        work_item_id="host-work", kind=kind, origin=origin, parent_work_item_id=parent
    )

    async def current(work):
        assert work.work_item_id == item.work_item_id

    monkeypatch.setattr(
        "app.services.chat_providers.pydantic_ai_provider._assert_work_output_authority_current",
        current,
    )
    result = await PydanticAIProvider()._try_light_turn(
        ctx,
        thread,
        "owner",
        SimpleNamespace(run_id=run.run_id, work_item_id=item.work_item_id),
    )
    assert result is None and calls == []
