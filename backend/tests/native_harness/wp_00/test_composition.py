"""No-network contract tests for the pinned Pydantic AI Harness release."""

from pathlib import Path

import pytest
from pydantic_ai import Agent
from pydantic_ai.capabilities import Instrumentation, ToolSearch
from pydantic_ai.models.test import TestModel
from pydantic_ai_harness import Planning, Skills, StepPersistence
from pydantic_ai_harness.step_persistence import InMemoryStepStore, continue_run


@pytest.mark.asyncio
async def test_core_capabilities_compose_stream_and_persist_history() -> None:
    """Selected Harness surfaces work with TestModel and a standard Core skill."""
    core_skills = Path(__file__).resolve().parents[4] / "agent/skills"
    store = InMemoryStepStore()
    agent = Agent(
        TestModel(call_tools=[]),
        instructions="Return one short test sentence.",
        capabilities=[
            Instrumentation(),
            ToolSearch(max_results=4),
            Planning(tools=["write_plan"]),
            Skills(core_skills, include={"integral-identity"}),
            StepPersistence(store=store, agent_name="integral-contract"),
        ],
    )

    async with agent.run_stream(
        "Reply with a short readiness sentence.",
        conversation_id="test-tenant/test-thread",
    ) as result:
        output = "".join([part async for part in result.stream_text(delta=True)])
        usage = result.usage
        message_count = len(result.all_messages())

    records = await store.list_runs(conversation_id="test-tenant/test-thread")
    assert output == "success (no tool calls)"
    assert usage.requests == 1
    assert len(records) == 1

    resumed_history = await continue_run(store, run_id=records[0].run_id)
    assert len(resumed_history) == message_count
