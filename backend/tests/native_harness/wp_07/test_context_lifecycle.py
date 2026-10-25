"""Cross-run context budgets exercised through fresh native Agents and checkpoints."""

import json
import math
import os
from dataclasses import replace

import httpx
import pytest
from openai import AsyncOpenAI
from pydantic_ai import Agent, BinaryContent, Tool
from pydantic_ai.capabilities import AbstractCapability, ToolSearch
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolAvailabilityDeltaPart,
    ToolCallPart,
    ToolReturnPart,
    ToolSearchReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import RequestUsage
from pydantic_ai.workspaces import LocalWorkspaceBackend
from pydantic_ai_harness import Skills, StepPersistence
from pydantic_ai_harness.step_persistence import InMemoryStepStore, continue_run

from app.agentive.harness.contracts import HarnessExecutionScope
from app.agentive.harness.pydantic_ai_compat import (
    IntegralContextAdmission,
    IntegralContextLifecycle,
    IntegralContextOverflow,
    IntegralContextPolicy,
    classify_integral_harness_exception,
)
from app.agentive.harness.pydantic_context import project_disclosures
from app.agentive.harness.runtime import build_native_runtime

pytestmark = pytest.mark.smoke


def _scope(run):
    return HarnessExecutionScope(
        tenant_id="context-workspace",
        workspace_id="context-workspace",
        principal_id="context-user",
        thread_id="context-thread",
        session_id="context-session",
        run_id=f"context-run-{run}",
        permission_revision="1",
        capability_version="1",
    )


def _size(messages, info):
    # Count model-visible parts and current instructions once, not historical
    # .instructions, usage, checkpoint metadata or other storage-only fields.
    return (
        len(str(info.instructions or ""))
        + len(
            json.dumps(
                [
                    [
                        getattr(part, "content", getattr(part, "args", ""))
                        for part in message.parts
                    ]
                    for message in messages
                ],
                default=str,
            )
        )
        + len(json.dumps([t.parameters_json_schema for t in info.function_tools]))
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("rotate", [False, True])
async def test_skills_plateau_across_fresh_runtime_checkpoint_resumes(tmp_path, rotate):
    store = InMemoryStepStore()
    runs = int(os.environ.get("INTEGRAL_CONTEXT_SOAK_RUNS", "100"))
    names = [f"context-skill-{i:03}" for i in range(runs)]
    for i, name in enumerate(names):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Workflow {i}.\n---\n"
            + f"SKILL_BODY_{i:03} "
            + "Use verified current records. " * 700
        )
    rows, summaries = [], []
    previous = None

    async def search_capabilities(query: str):
        return {"results": []}

    tools = [Tool(search_capabilities, metadata={"integral_op_class": "read"})]
    for run in range(runs):
        calls = []
        selected = names[run if rotate else 0]

        def respond(messages, info):
            if "You are a context summarization assistant" in str(messages):
                summaries.append(run)
                return ModelResponse(
                    parts=[TextPart("Only verified records. Keep user constraints.")],
                    usage=RequestUsage(input_tokens=1000, output_tokens=20),
                )
            size = _size(messages, info)
            assert f"Workflow {runs - 1}." not in str(
                info.instructions
            ), "The native full catalogue bypassed Core's bounded discovery projection"
            rows.append(size)
            calls.append(size)
            if len(calls) == 1:
                return ModelResponse(
                    parts=[ToolCallPart("load_capability", {"id": selected})],
                    usage=RequestUsage(
                        input_tokens=math.ceil(size / 4), output_tokens=20
                    ),
                )
            return ModelResponse(
                parts=[TextPart("Verified task complete.")],
                usage=RequestUsage(input_tokens=math.ceil(size / 4), output_tokens=10),
            )

        scope = _scope(run)
        agent, scoped = build_native_runtime(
            model=FunctionModel(respond),
            instructions="Use verified records.",
            tools=tools,
            step_store_backend=store,
            scope=scope,
            agent_name="context-probe",
            skill_directories=[tmp_path],
            allowed_skill_names=names,
        )
        history = await continue_run(scoped, run_id=previous) if previous else []
        # Genuine JSON round trip, as with a restarted process.
        history = ModelMessagesTypeAdapter.validate_json(
            ModelMessagesTypeAdapter.dump_json(history)
        )
        result = await agent.run(
            f"Task {run}: use {selected}.",
            message_history=history,
            conversation_id=scope.framework_conversation_id,
            run_id=scope.framework_run_id,
        )
        previous = scope.run_id
        restored = await continue_run(scoped, run_id=previous)
        assert ModelMessagesTypeAdapter.dump_json(
            restored
        ) == ModelMessagesTypeAdapter.dump_json(result.all_messages())
    assert max(rows) < 115_000
    assert max(rows[-40:]) < max(rows[10:50]) + 15_000
    # Check the real outgoing provider JSON after the soak, not just a Python
    # history estimate. Mock transport exercises OpenAIChatModel serialization.
    wires = []

    def handler(request):
        body = request.content.decode()
        if "You are a context summarization assistant" not in body:
            wires.append(
                (
                    len(request.content),
                    sum(f"SKILL_BODY_{i:03}" in body for i in range(runs)),
                )
            )
        return httpx.Response(
            200,
            json={
                "id": "probe",
                "object": "chat.completion",
                "model": "gpt-4.1",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "Complete."},
                    }
                ],
                "usage": {
                    "prompt_tokens": 1000,
                    "completion_tokens": 10,
                    "total_tokens": 1010,
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        native = OpenAIChatModel(
            "gpt-4.1",
            provider=OpenAIProvider(
                openai_client=AsyncOpenAI(
                    api_key="synthetic-test-key", http_client=http
                )
            ),
        )
        scope = _scope("wire")
        agent, scoped = build_native_runtime(
            model=native,
            instructions="Use verified records.",
            tools=tools,
            step_store_backend=store,
            scope=scope,
            agent_name="context-probe",
            skill_directories=[tmp_path],
            allowed_skill_names=names,
        )
        await agent.run(
            "Report the result.",
            message_history=await continue_run(scoped, run_id=previous),
        )
    assert wires and wires[-1][0] < 115_000
    assert wires[-1][1] <= 4
    print(
        "CONTEXT_SOAK",
        json.dumps(
            {
                "runs": runs,
                "rotating": rotate,
                "max_request_chars": max(rows),
                "last_40_max_chars": max(rows[-40:]),
                "wire_bytes": wires[-1][0],
                "retained_skill_bodies": wires[-1][1],
                "summary_calls": len(summaries),
            }
        ),
    )


def test_old_tool_search_disclosures_are_bounded_and_idempotent():
    history = []
    for i in range(100):
        history.extend(
            [
                ModelRequest(parts=[UserPromptPart(f"Task {i}")]),
                ModelResponse(parts=[ToolCallPart("search_tools", {}, str(i))]),
                ModelRequest(
                    parts=[
                        ToolSearchReturnPart(
                            content={
                                "discovered_tools": [
                                    {
                                        "name": f"tool_{i}",
                                        "description": "read",
                                        "parameters_json_schema": {"type": "object"},
                                    }
                                ]
                            },
                            tool_call_id=str(i),
                        ),
                        ToolAvailabilityDeltaPart(tools_added=[f"tool_{i}"]),
                    ]
                ),
            ]
        )
    projected = project_disclosures(history, IntegralContextPolicy())
    names = {
        name
        for message in projected
        for part in message.parts
        for name in (
            part.tools_added if isinstance(part, ToolAvailabilityDeltaPart) else []
        )
    }
    assert len(names) <= 16
    assert project_disclosures(projected, IntegralContextPolicy()) == projected
    ModelMessagesTypeAdapter.validate_json(
        ModelMessagesTypeAdapter.dump_json(projected)
    )
    assert len(history[-1].parts[1].tools_added) == 1


@pytest.mark.asyncio
async def test_irreducible_input_stops_before_inference():
    called = []

    def respond(messages, info):
        called.append(True)
        return ModelResponse(parts=[TextPart("Should not run")])

    agent = Agent(
        FunctionModel(respond),
        capabilities=[
            IntegralContextAdmission(
                policy=replace(IntegralContextPolicy(), input_tokens=100)
            )
        ],
    )
    with pytest.raises(IntegralContextOverflow) as failure:
        await agent.run("accepted user input " * 200)
    assert not called
    assert classify_integral_harness_exception(failure.value) == "harness_context_limit"


@pytest.mark.asyncio
async def test_normalization_preserves_pending_staging_and_current_parallel_effects():
    history = [
        ModelRequest(parts=[UserPromptPart("Create my records")]),
        ModelResponse(parts=[ToolCallPart("write", {}, "effect")]),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    "write", {"_kind": "staged_change", "token": "pending"}, "effect"
                )
            ]
        ),
    ]
    for i in range(20):
        history.extend(
            [
                ModelRequest(parts=[UserPromptPart(f"Follow-up {i}")]),
                ModelResponse(parts=[TextPart("No changes")]),
            ]
        )
    assert (
        project_disclosures(
            history, IntegralContextPolicy(skill_bytes=0, revealed_tools=0)
        )
        == history
    )
    assert ModelMessagesTypeAdapter.dump_json(
        history
    ) == ModelMessagesTypeAdapter.dump_json(
        project_disclosures(history, IntegralContextPolicy())
    )


@pytest.mark.asyncio
async def test_expired_tools_can_be_discovered_and_executed_again():
    executed, visible = [], []

    def make_read(index):
        async def read():
            executed.append(index)
            return {"verified": index}

        return Tool(
            read,
            name=f"read_{index}",
            description=f"Read {index} only",
            defer_loading=True,
        )

    tools = [make_read(i) for i in range(40)]

    class Inspect(AbstractCapability):
        async def before_model_request(self, ctx, request_context):
            visible.append(
                len(request_context.model_request_parameters.revealed_tool_names)
            )
            return request_context

    history = []
    for selected in [*range(40), 0]:
        calls = []

        def respond(messages, info):
            calls.append(True)
            if len(calls) == 1:
                return ModelResponse(
                    parts=[
                        ToolCallPart("search_tools", {"queries": [f"read_{selected}"]})
                    ]
                )
            if len(calls) == 2:
                assert f"read_{selected}" in str(messages[-1].parts), str(
                    messages[-1].parts
                )
                return ModelResponse(parts=[ToolCallPart(f"read_{selected}", {})])
            return ModelResponse(parts=[TextPart("Verified")])

        result = await Agent(
            FunctionModel(respond),
            tools=tools,
            capabilities=[
                ToolSearch(strategy="keywords", max_results=1),
                IntegralContextLifecycle(),
                Inspect(),
            ],
        ).run(f"Read {selected}", message_history=history)
        history = ModelMessagesTypeAdapter.validate_json(
            ModelMessagesTypeAdapter.dump_json(result.all_messages())
        )
    assert executed == [*range(40), 0]
    assert max(visible) <= 18


@pytest.mark.asyncio
async def test_completed_unknown_effect_is_not_cheap_cleared():
    from app.agentive.harness.pydantic_ai_compat import (
        build_integral_context_compaction,
    )

    history = [ModelRequest(parts=[UserPromptPart("Earlier work")])]
    for index in range(8):
        history.extend(
            [
                ModelResponse(
                    parts=[ToolCallPart("unknown_effect", {"id": index}, str(index))]
                ),
                ModelRequest(
                    parts=[
                        ToolReturnPart("unknown_effect", {"receipt": index}, str(index))
                    ]
                ),
            ]
        )
    history.append(ModelRequest(parts=[UserPromptPart("Next task")]))
    result = await build_integral_context_compaction(
        clearable_tools=frozenset({"read"})
    ).compact(history, None)
    assert result == history


@pytest.mark.asyncio
async def test_latest_oversized_read_has_explicit_bounded_preview():
    from app.agentive.harness.pydantic_ai_compat import (
        build_integral_context_compaction,
    )

    history = [
        ModelRequest(parts=[UserPromptPart("Read the large file")]),
        ModelResponse(parts=[ToolCallPart("read", {"id": "file"}, "read")]),
        ModelRequest(parts=[ToolReturnPart("read", "r" * 500_000, "read")]),
    ]
    projected = await build_integral_context_compaction(
        clearable_tools=frozenset({"read"})
    ).compact(history, None)
    assert len(projected[-1].parts[0].content) < 17_000
    assert "not the complete result" in projected[-1].parts[0].content
    assert projected[-2] == history[-2]
    assert len(history[-1].parts[0].content) == 500_000


@pytest.mark.asyncio
async def test_existing_bloated_checkpoint_normalizes_without_losing_archive(tmp_path):
    names = [f"old-workflow-{i}" for i in range(20)]
    backing, history, previous = InMemoryStepStore(), [], None
    for name in names:
        folder = tmp_path / name
        folder.mkdir()
        (folder / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Workflow.\n---\n"
            + name
            + " evidence " * 2000
        )
    for i, name in enumerate(names):
        scope, count = _scope(i), []

        def respond(messages, info):
            count.append(True)
            return ModelResponse(
                parts=(
                    [ToolCallPart("load_capability", {"id": name})]
                    if len(count) == 1
                    else [TextPart("Done")]
                )
            )

        _, scoped = build_native_runtime(
            model=FunctionModel(respond),
            instructions="Read only.",
            tools=[],
            step_store_backend=backing,
            scope=scope,
            agent_name="legacy",
        )
        # Reproduce the old retention behavior using unmodified native Skills,
        # with no Core context normalization or admission applied.
        legacy = Agent(
            FunctionModel(respond),
            capabilities=[
                Skills(tmp_path, workspace=LocalWorkspaceBackend(tmp_path)),
                StepPersistence(
                    store=scoped, run_id=scope.framework_run_id, agent_name="legacy"
                ),
            ],
        )
        await legacy.run(
            f"Use {name}",
            message_history=history,
            run_id=scope.framework_run_id,
            conversation_id=scope.framework_conversation_id,
        )
        history = await continue_run(scoped, run_id=scope.run_id)
        previous = scope.run_id
    original = ModelMessagesTypeAdapter.dump_json(history)
    seen = []

    def fixed(messages, info):
        if "You are a context summarization assistant" in str(messages):
            return ModelResponse(parts=[TextPart("Only verified evidence; read only.")])
        seen.append(_size(messages, info))
        assert names[0] not in str(
            [
                p.content
                for m in messages
                for p in m.parts
                if isinstance(p, ToolReturnPart)
            ]
        )
        return ModelResponse(parts=[TextPart("Recovered without replaying effects")])

    scope = _scope("fixed")
    agent, scoped = build_native_runtime(
        model=FunctionModel(fixed),
        instructions="Read only.",
        tools=[],
        step_store_backend=backing,
        scope=scope,
        agent_name="fixed",
        skill_directories=[tmp_path],
        allowed_skill_names=names,
    )
    restored = await continue_run(scoped, run_id=previous)
    await agent.run(
        "Continue read-only",
        message_history=restored,
        run_id=scope.framework_run_id,
        conversation_id=scope.framework_conversation_id,
    )
    assert seen and max(seen) < 115_000
    assert (
        ModelMessagesTypeAdapter.dump_json(await continue_run(scoped, run_id=previous))
        == original
    )


@pytest.mark.asyncio
async def test_image_bytes_are_not_mistaken_for_text_tokens():
    called = []

    def respond(messages, info):
        called.append(True)
        return ModelResponse(parts=[TextPart("Image preserved")])

    image = BinaryContent(
        data=b"synthetic image payload" * 50_000, media_type="image/png"
    )
    result = await Agent(
        FunctionModel(respond), capabilities=[IntegralContextAdmission()]
    ).run(["Describe", image])
    assert called and result.output == "Image preserved"


@pytest.mark.asyncio
async def test_oversized_summary_stops_before_inference_instead_of_retrying():
    from app.agentive.harness.pydantic_ai_compat import (
        build_integral_history_compaction,
    )

    called = []

    def respond(messages, info):
        called.append(True)
        return ModelResponse(parts=[TextPart("Should not run")])

    history = [
        ModelRequest(parts=[UserPromptPart("Earlier task")]),
        ModelResponse(parts=[TextPart("old explanation " * 30_000)]),
        ModelRequest(parts=[UserPromptPart("A later completed task")]),
        ModelResponse(parts=[TextPart("Completed")]),
    ]
    summary = build_integral_history_compaction()
    # Force a nested summary request: native 0.59 otherwise preserves an
    # indivisible large recent message in the configured retained suffix.
    summary.keep_tokens = 0
    summary.keep_messages = 1
    agent = Agent(FunctionModel(respond), capabilities=[summary])
    with pytest.raises(IntegralContextOverflow):
        await agent.run("Next task", message_history=history)
    assert called == []
    assert len(history[1].parts[0].content) == 480_000
