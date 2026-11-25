"""An observed Pydantic routing pass can publish ordinary words before completion."""

import asyncio

import pytest
from pydantic_ai.models.function import DeltaThinkingPart, DeltaToolCall, FunctionModel

from app.agentive.harness.adaptive import build_light_agent
from app.agentive.harness.conversation_preview import ConversationPreview
from app.services.model_retry_status import stream_with_retry_status


@pytest.mark.asyncio
@pytest.mark.parametrize("prompted", [False, True])
async def test_partial_routed_answer_precedes_completion_without_repeating(prompted):
    release = asyncio.Event()
    checked = []
    requests = []

    async def infer(messages, info):
        requests.append(info)
        yield {0: DeltaThinkingPart(content="private reasoning")}
        if prompted:
            yield '{"route":"answer_conversation","text":"Hello world'
        else:
            yield {
                1: DeltaToolCall(
                    name="answer_conversation", json_args='{"text":"Hello world'
                )
            }
        await asyncio.wait_for(release.wait(), 3)
        if prompted:
            yield '"}'
        else:
            yield {1: DeltaToolCall(json_args='","category":"social"}')}

    async def authorize():
        checked.append(True)

    model = FunctionModel(
        stream_function=infer, profile={"supports_forced_tool_choice": not prompted}
    )
    agent = build_light_agent(model)
    preview = ConversationPreview(prompted=prompted, authorize=authorize)

    async def turn():
        result = await agent.run("Hello", event_stream_handler=preview.observe)
        assert result.output == "Hello world"
        yield {"type": "text-delta", "delta": result.output[len(preview.sent) :]}

    stream = stream_with_retry_status(turn())
    try:
        assert await anext(stream) == {"type": "text-delta", "delta": "Hello "}
        release.set()
        assert [item async for item in stream] == [
            {"type": "text-delta", "delta": "world"}
        ]
        assert len(requests) == len(checked) == 1
    finally:
        release.set()
        await stream.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,args",
    [
        ("request_operational_context", '{"skill_id":"sensitive id"}'),
        ("propose_simple_reminder", '{"text":"unreviewed private reminder"}'),
        ("answer_conversation", '{"text":"<tool_call private details"}'),
        ("answer_conversation", '{"text":"See n.Entry.0123456789abcdef01234567 here"}'),
        (
            "answer_conversation",
            '{"text":"See o.OtherModel.0123456789abcdef01234567 here"}',
        ),
    ],
)
async def test_other_routes_and_protocols_have_no_provisional_text(name, args):
    from pydantic_ai.messages import PartStartEvent, ToolCallPart

    async def events():
        yield PartStartEvent(index=0, part=ToolCallPart(name, args))

    async def deny():
        pytest.fail("No frame may be proposed")

    preview = ConversationPreview(prompted=False, authorize=deny)
    await preview.observe(None, events())
    assert preview.sent == ""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "protected,injected,expected_frames",
    [
        (False, "", ["Hello ", "world"]),
        (True, "", ["Hello world"]),
        (False, "Private context from the host.", ["Hello world"]),
    ],
)
async def test_administrator_policy_applies_at_native_turn_entry(
    monkeypatch, protected, injected, expected_frames
):
    from dataclasses import replace
    from unittest.mock import AsyncMock

    from pydantic_ai.messages import ModelResponse, ToolCallPart

    from app.agentive.work_models import WorkItem
    from app.schemas.deployment_chat_limits import ChatLimitsWrite
    from app.services.chat_providers.pydantic_ai_provider import PydanticAIProvider
    from app.services.deployment_chat_limits import read_limits, save_limits
    from tests.native_harness.test_adaptive_context import setup_turn

    ctx, thread, run, _ = await setup_turn(monkeypatch)
    ctx = replace(ctx, system_context=injected)
    work = await WorkItem.create(
        work_item_id="preview", kind="chat_turn", origin="interactive_chat"
    )
    ctx.extra_data["work_execution_context"] = dict(
        work_item_id=work.work_item_id,
        run_id=run.run_id,
        principal_id=ctx.user_id,
        workspace_id=ctx.workspace_id,
        thread_id=thread.id,
        attempt=1,
        lease_fence=1,
        logical_step_key="step",
        effect_key="effect",
        lease_token="lease",
    )
    await save_limits(
        "admin",
        ChatLimitsWrite(
            revision=(await read_limits())["revision"],
            overrides={},
            whole_answer_protection=protected,
        ),
    )
    calls = []

    def answer(messages, info):
        calls.append("completed")
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "answer_conversation", {"text": "Hello world", "category": "social"}
                )
            ]
        )

    async def stream(messages, info):
        calls.append("streamed")
        yield {
            0: DeltaToolCall(
                name="answer_conversation", json_args='{"text":"Hello world'
            )
        }
        yield {0: DeltaToolCall(json_args='","category":"social"}')}

    boundary = "app.services.chat_providers.pydantic_ai_provider."
    monkeypatch.setattr(
        boundary + "build_litellm_sdk_model",
        lambda **_: FunctionModel(answer, stream_function=stream),
    )
    monkeypatch.setattr(boundary + "_assert_work_output_authority_current", AsyncMock())
    events = [e async for e in PydanticAIProvider().stream_turn(ctx)]
    assert [e["delta"] for e in events if e["type"] == "text-delta"] == expected_frames
    assert sum(e["type"] == "message-finish" for e in events) == 1
    assert calls == ["streamed" if len(expected_frames) == 2 else "completed"]


@pytest.mark.asyncio
async def test_authority_loss_between_chunks_withholds_next_words(monkeypatch):
    from unittest.mock import AsyncMock

    from pydantic_ai.messages import (
        PartDeltaEvent,
        PartStartEvent,
        ToolCallPart,
        ToolCallPartDelta,
    )

    from app.agentive.harness import conversation_preview

    clock = iter([1.0, 1.0, 2.0])
    monkeypatch.setattr(conversation_preview, "monotonic", lambda: next(clock))
    publish = AsyncMock()
    monkeypatch.setattr(conversation_preview, "emit_conversation_preview", publish)
    authorize = AsyncMock(side_effect=[None, PermissionError("revoked")])
    preview = ConversationPreview(prompted=False, authorize=authorize)

    async def events():
        yield PartStartEvent(
            index=0, part=ToolCallPart("answer_conversation", '{"text":"First ')
        )
        yield PartDeltaEvent(
            index=0, delta=ToolCallPartDelta(args_delta="private words ")
        )

    with pytest.raises(PermissionError):
        await preview.observe(None, events())
    publish.assert_awaited_once_with(delta="First ")
