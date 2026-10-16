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
    ctx = SimpleNamespace(active_capability_ids=set(), tools={})
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
