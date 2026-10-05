"""No-network contract test for host-owned deferred approval continuation."""

import pytest
from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults, ToolDenied
from pydantic_ai.models.test import TestModel


@pytest.mark.asyncio
async def test_deferred_tool_call_can_resume_from_transcript() -> None:
    """Approval returns to the host, and a later run can continue the transcript."""
    agent = Agent(
        TestModel(call_tools=["publish_change"]),
        output_type=[str, DeferredToolRequests],
    )

    @agent.tool_plain(requires_approval=True)
    def publish_change(change_id: str) -> str:
        return f"published {change_id}"

    pending = await agent.run("Publish change C-42")
    assert isinstance(pending.output, DeferredToolRequests)
    assert len(pending.output.approvals) == 1
    pending_call = pending.output.approvals[0]

    resumed = await agent.run(
        message_history=pending.all_messages(),
        deferred_tool_results=DeferredToolResults(
            approvals={
                pending_call.tool_call_id: ToolDenied("denied by Integral policy")
            }
        ),
    )

    assert isinstance(resumed.output, str)
    assert len(resumed.all_messages()) > len(pending.all_messages())
