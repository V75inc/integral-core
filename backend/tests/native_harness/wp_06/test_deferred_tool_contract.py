"""Probe the pinned Pydantic AI deferred-approval contract.

This is a library contract test, not an Integral approval integration test.
"""

import pytest
from pydantic_ai import Agent, CallDeferred
from pydantic_ai.messages import (
    DeferredToolRequestsEvent,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults, Tool
from pydantic_ai_harness.step_persistence import InMemoryStepStore, StepPersistence


@pytest.mark.asyncio
@pytest.mark.parametrize("approved", [True, False])
async def test_deferred_approval_resumes_from_serialized_history(approved):
    """A fresh agent resumes an approved or denied call without early effects."""
    executed = []

    async def file_document(name: str):
        executed.append(name)
        return {"filed": name}

    def respond(messages, _info):
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if not returns:
            return ModelResponse(
                parts=[ToolCallPart("file_document", {"name": "guide.docx"}, "call-1")]
            )
        return ModelResponse(parts=[TextPart("Continued after the decision.")])

    def agent():
        return Agent(
            FunctionModel(respond),
            tools=[Tool(file_document, requires_approval=True)],
            output_type=[str, DeferredToolRequests],
        )

    first = await agent().run("File the guide and finish the task.")
    assert isinstance(first.output, DeferredToolRequests)
    assert [call.tool_call_id for call in first.output.approvals] == ["call-1"]
    assert not executed

    # The host must durably store and authenticate its own decision and history.
    # A fresh agent and JSON round trip only prove the upstream resume API.
    history = ModelMessagesTypeAdapter.validate_json(
        ModelMessagesTypeAdapter.dump_json(first.all_messages())
    )
    resumed = await agent().run(
        message_history=history,
        deferred_tool_results=DeferredToolResults(approvals={"call-1": approved}),
    )
    assert resumed.output == "Continued after the decision."
    assert executed == (["guide.docx"] if approved else [])


@pytest.mark.asyncio
async def test_streamed_deferred_call_requires_interrupted_frontier_and_reconciliation():
    """Native frontier retains the call, but does not settle its effect ledger."""
    store = InMemoryStepStore()
    calls = []

    async def stage_document(name: str):
        calls.append(name)
        # Simulate a staged proposal already committed by Integral before the
        # native call is deferred. The metadata is not approval authority.
        raise CallDeferred(metadata={"proposal_ref": "synthetic-proposal"})

    async def stream(messages, _info):
        has_result = any(
            isinstance(part, ToolReturnPart)
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
        )
        if has_result:
            yield "Continued from the exact result."
        else:
            yield {
                0: DeltaToolCall(
                    name="stage_document",
                    json_args='{"name":"guide.docx"}',
                    tool_call_id="call-1",
                )
            }

    def agent(run_id):
        return Agent(
            FunctionModel(stream_function=stream),
            tools=[stage_document],
            output_type=[str, DeferredToolRequests],
            capabilities=[
                StepPersistence(store=store, run_id=run_id, capture_frontier=True)
            ],
        )

    events = []
    async with agent("original").run_stream_events("File the guide.") as response:
        async for event in response:
            events.append(event)
    deferred = next(
        event for event in events if isinstance(event, DeferredToolRequestsEvent)
    )
    assert [call.tool_call_id for call in deferred.requests.calls] == ["call-1"]
    assert deferred.requests.metadata["call-1"] == {
        "proposal_ref": "synthetic-proposal"
    }
    complete = await store.latest_snapshot(run_id="original")
    frontier = await store.latest_snapshot(run_id="original", include_interrupted=True)
    assert complete is not None and len(complete.messages) == 1
    assert frontier is not None and frontier.state == "interrupted"
    assert len(frontier.messages) == 2
    unresolved = await store.list_unresolved_tool_effects(run_id="original")
    assert [(effect.tool_call_id, effect.status) for effect in unresolved] == [
        ("call-1", "started")
    ]

    # A host must authenticate the applied receipt before constructing this
    # result. Pydantic resumes the original call without executing it twice.
    resumed = []
    async with agent("resume").run_stream_events(
        message_history=frontier.messages,
        deferred_tool_results=DeferredToolResults(
            calls={"call-1": {"state": "consumed"}}
        ),
    ) as response:
        async for event in response:
            resumed.append(event)
    assert calls == ["guide.docx"]
    assert any(type(event).__name__ == "AgentRunResultEvent" for event in resumed)
    assert await store.latest_snapshot(run_id="resume") is not None
    # Completing a later Pydantic run does not by itself reconcile the first
    # run's started effect record: Integral must bind and settle that evidence.
    assert len(await store.list_unresolved_tool_effects(run_id="original")) == 1
