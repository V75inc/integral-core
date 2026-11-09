"""Contract probe for steering through Core's native event stream."""

import pytest
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import EnqueuedMessagesEvent, UserPromptPart
from pydantic_ai.models.test import TestModel
from pydantic_ai_harness import StepPersistence
from pydantic_ai_harness.step_persistence import InMemoryStepStore, continue_run


@pytest.mark.asyncio
async def test_streamed_steering_delivers_and_persists_correction() -> None:
    store = InMemoryStepStore()
    agent = Agent(
        TestModel(call_tools=["lookup"]),
        capabilities=[StepPersistence(store=store, agent_name="steering-contract")],
    )
    accepted = []

    @agent.tool
    async def lookup(ctx: RunContext[None]) -> str:
        accepted.append(ctx.enqueue("Use the revised destination.", priority="asap"))
        return "Lookup completed."

    async with agent.run_stream_events(
        "Prepare a draft.", conversation_id="steering-contract"
    ) as stream:
        delivered = [
            event.enqueue_id
            async for event in stream
            if isinstance(event, EnqueuedMessagesEvent)
        ]

    assert len(accepted) == 1 and accepted[0] is not None
    assert delivered == accepted
    (record,) = await store.list_runs(conversation_id="steering-contract")
    history = await continue_run(store, run_id=record.run_id)
    assert [
        part.content
        for message in history
        for part in message.parts
        if isinstance(part, UserPromptPart)
    ] == ["Prepare a draft.", "Use the revised destination."]
