"""Regression probes for model working-context growth and turn boundaries."""

from types import SimpleNamespace

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel
from pydantic_ai_harness.compaction import SummarizingCompaction
from pydantic_ai_harness.step_persistence import InMemoryStepStore, StepPersistence

from app.agentive.harness.pydantic_ai_compat import (
    IntegralHistorySummary,
    IntegralToolResultCompaction,
)


def _completed_reads(count=8):
    history = [ModelRequest(parts=[UserPromptPart("Earlier task")])]
    for index in range(count):
        call_id = str(index)
        history.extend(
            [
                ModelResponse(parts=[ToolCallPart("read", {}, call_id)]),
                ModelRequest(parts=[ToolReturnPart("read", "record " * 1000, call_id)]),
            ]
        )
    return history


@pytest.mark.asyncio
async def test_clearing_survives_in_working_history_and_result():
    """A later checkpoint must not resurrect cleared completed-turn payloads."""
    history = _completed_reads()
    store = InMemoryStepStore()

    def respond(messages, info):
        assert messages[2].parts[0].content == "[tool result cleared]"
        return ModelResponse(parts=[TextPart("Done")])

    agent = Agent(
        FunctionModel(respond),
        capabilities=[
            StepPersistence(store=store, run_id="context-budget-probe"),
            IntegralToolResultCompaction(max_tokens=1, keep_pairs=0),
        ],
    )
    result = await agent.run("New task", message_history=history)
    assert result.all_messages()[2].parts[0].content == "[tool result cleared]"
    snapshots = await store.list_snapshots(run_id="context-budget-probe")
    assert snapshots
    assert snapshots[-1].messages[2].parts[0].content == "[tool result cleared]"
    assert history[2].parts[0].content == "record " * 1000


@pytest.mark.asyncio
async def test_host_event_ends_prior_active_turn_without_fabricating_user_text():
    """A typed event frees completed data and keeps the new receipt exact."""
    history = _completed_reads()
    host_event = ModelRequest(
        parts=[SystemPromptPart("Report the current approval outcome.")],
        metadata={"integral_host_turn_v1": "server-run-id"},
    )
    current = [
        host_event,
        ModelResponse(parts=[ToolCallPart("read_receipt", {}, "receipt")]),
        ModelRequest(
            parts=[ToolReturnPart("read_receipt", {"state": "consumed"}, "receipt")]
        ),
    ]
    compacted = await IntegralToolResultCompaction(max_tokens=1, keep_pairs=0).compact(
        history + current, SimpleNamespace()
    )
    assert compacted[2].parts[0].content == "[tool result cleared]"
    assert compacted[-3:] == current
    assert not any(
        isinstance(part, UserPromptPart)
        for message in current
        for part in message.parts
    )


@pytest.mark.asyncio
async def test_history_summary_uses_same_host_event_boundary(monkeypatch):
    """Summarization and cheap clearing agree about the current task."""
    history = _completed_reads()
    current = [
        ModelRequest(
            parts=[SystemPromptPart("New scheduled event")],
            metadata={"integral_host_turn_v1": "server-run-id"},
        )
    ]
    seen = []

    async def summarize(self, messages, ctx):
        seen.extend(messages)
        return [ModelRequest(parts=[SystemPromptPart("Summary")])]

    monkeypatch.setattr(SummarizingCompaction, "compact", summarize)
    ctx = SimpleNamespace(
        active_capability_ids=set(),
        tools={"read": SimpleNamespace(metadata={"integral_op_class": "read"})},
    )
    compacted = await IntegralHistorySummary(max_tokens=1, keep_messages=1).compact(
        history + current, ctx
    )
    assert len(seen) == len(history)
    assert seen != history  # Completed tool payloads are removed before summarizing.
    assert compacted[-1] == current[0]


@pytest.mark.asyncio
async def test_unmarked_system_data_cannot_end_active_task():
    """Only Core's typed event marker splits a textless turn, never its prose."""
    active = _completed_reads()
    active.append(ModelRequest(parts=[SystemPromptPart("New scheduled event")]))
    compacted = await IntegralToolResultCompaction(max_tokens=1, keep_pairs=0).compact(
        active, SimpleNamespace()
    )
    assert compacted == active


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool", ["integral_governed_query", "integral_call_workspace_tool"]
)
async def test_current_task_keeps_small_facts_across_read_transports(tool):
    """Several fresh source facts must survive synthesis without archive recall."""
    from app.agentive.harness.pydantic_ai_compat import (
        build_integral_context_compaction,
    )

    messages = [ModelRequest(parts=[UserPromptPart("Summarize these sources")])]
    facts = []
    for index in range(6):
        fact = {
            "source": f"source-{index}",
            "count": index + 1,
            "coverage": "Complete visible population. " * 45,
        }
        facts.append(fact)
        messages.extend(
            [
                ModelResponse(
                    parts=[ToolCallPart(tool, {"source": index}, str(index))]
                ),
                ModelRequest(
                    parts=[
                        ToolReturnPart(
                            tool,
                            fact,
                            str(index),
                            metadata={"integral_effect_v1": "read"},
                        )
                    ]
                ),
            ]
        )
    compactor = build_integral_context_compaction(clearable_tools=frozenset({tool}))
    compacted = await compactor.compact(messages, SimpleNamespace())
    retained = [
        part.content
        for message in compacted
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    assert retained == facts


@pytest.mark.asyncio
async def test_native_working_context_can_synthesize_six_current_source_facts():
    from app.agentive.harness.pydantic_ai_compat import build_integral_working_context

    requests = []

    async def read_fact(index: int) -> dict:
        return {
            "source": index,
            "count": index + 1,
            "coverage": "Complete visible population. " * 45,
        }

    def respond(messages, info):
        requests.append(messages)
        if len(requests) <= 6:
            return ModelResponse(
                parts=[ToolCallPart("read_fact", {"index": len(requests) - 1})]
            )
        returns = [
            part.content
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        assert [value["count"] for value in returns] == [1, 2, 3, 4, 5, 6]
        return ModelResponse(parts=[TextPart("All six source counts are available.")])

    agent = Agent(
        FunctionModel(respond),
        tools=[read_fact],
        capabilities=[
            build_integral_working_context(clearable_tools=frozenset({"read_fact"}))
        ],
    )
    result = await agent.run("Give me an overview of these sources")
    assert result.output == "All six source counts are available."
    assert len(requests) == 7


@pytest.mark.asyncio
async def test_current_fact_budget_survives_interleaved_large_results():
    from app.agentive.harness.pydantic_ai_compat import (
        build_integral_context_compaction,
    )

    tool = "integral_governed_query"
    messages = [ModelRequest(parts=[UserPromptPart("Summarize " * 10000)])]
    facts = [{"source": index, "count": index + 1} for index in range(36)]
    for index, content in enumerate(facts + ["large record " * 2000] * 10):
        messages.extend(
            [
                ModelResponse(parts=[ToolCallPart(tool, {}, str(index))]),
                ModelRequest(
                    parts=[
                        ToolReturnPart(
                            tool,
                            content,
                            str(index),
                            metadata={"integral_effect_v1": "read"},
                        )
                    ]
                ),
            ]
        )
    compactor = build_integral_context_compaction(clearable_tools=frozenset({tool}))
    compacted = await compactor.compact(messages, SimpleNamespace())
    returns = [
        part.content
        for message in compacted
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    assert returns[:4] == [compactor.placeholder] * 4
    assert returns[4:36] == facts[4:]
    assert returns[36:44] == [compactor.placeholder] * 8
    assert all("preview" in str(content).lower() for content in returns[-2:])
