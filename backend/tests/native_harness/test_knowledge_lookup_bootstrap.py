"""Focused knowledge reads do not require expanding a complete workflow."""

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import FunctionModel
from pydantic_ai_harness.step_persistence import InMemoryStepStore

from app.agentive.harness.broker_tools import build_brokered_tools
from app.agentive.harness.runtime import build_native_runtime
from app.agentive.tooling.catalogue import build_tool_catalogue
from app.schemas.capability_broker import CapabilityResult
from tests.native_harness.wp_06.test_provider_stream import _scope

pytestmark = [pytest.mark.asyncio, pytest.mark.smoke]


@pytest.mark.parametrize("allowed", [True, False])
async def test_native_lookup_uses_the_existing_broker_without_discovery_roundtrips(
    tmp_path, monkeypatch, allowed
):
    skill = tmp_path / "sample-knowledge"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: sample-knowledge\ndescription: Read and save reports.\n"
        "allowed-tools: integral_query integral_save_view\n---\n"
        "Read evidence and propose a saved report if requested.\n"
    )
    calls = []

    async def invoke(**kwargs):
        calls.append(kwargs)
        return CapabilityResult(
            ok=allowed,
            data=(
                {
                    "results": [
                        {
                            "snippet": "Service every six months.",
                            "action_url": "/tracks/t?entry=e",
                        }
                    ]
                }
                if allowed
                else {}
            ),
            error_code="" if allowed else "insufficient_permissions",
        )

    monkeypatch.setattr(
        "app.agentive.services.capability_broker.invoke_declared_capability", invoke
    )
    catalogue = [
        item
        for item in build_tool_catalogue()
        if item["name"] in {"integral_query", "integral_save_view"}
    ]
    tools = build_brokered_tools(
        scope=_scope("lookup"), catalogue=catalogue, skill_library=tmp_path
    )
    requests = []

    def respond(messages, info):
        requests.append(messages)
        visible = {tool.name for tool in info.function_tools}
        assert "integral_save_view" not in visible
        if len(requests) == 1:
            assert "integral_query" in visible
            assert "integral_query is available directly" in str(info.instructions)
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "integral_query",
                        {
                            "query": "service interval",
                            "top_n": 3,
                            "context_tokens": 600,
                        },
                    )
                ]
            )
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        assert any(part.tool_name == "integral_query" for part in returns)
        if not allowed:
            assert "Service every six months" not in str(returns)
        return ModelResponse(
            parts=[
                TextPart(
                    "Service every six months."
                    if allowed
                    else "The search is unavailable to me."
                )
            ]
        )

    agent, _ = build_native_runtime(
        model=FunctionModel(respond),
        instructions="Find a saved fact and cite its source.",
        tools=tools,
        scope=_scope("lookup"),
        agent_name="lookup-test",
        skill_directories=[tmp_path],
        allowed_skill_names={"sample-knowledge"},
        step_store_backend=InMemoryStepStore(),
    )
    await agent.run(
        "Find the service interval.",
        run_id=_scope("lookup").framework_run_id,
        conversation_id=_scope("lookup").framework_conversation_id,
    )
    assert len(requests) == 2
    assert len(calls) == 1 and calls[0]["capability_key"] == "integral_query"
    assert calls[0]["principal_id"] == _scope("lookup").principal_id
    assert calls[0]["workspace_id"] == _scope("lookup").workspace_id


async def test_lookup_bootstrap_respects_explicit_host_visibility_and_catalogue():
    catalogue = [
        item for item in build_tool_catalogue() if item["name"] == "integral_query"
    ]
    tools = build_brokered_tools(
        scope=_scope("restricted"), catalogue=catalogue, always_available_tools=()
    )
    assert next(tool for tool in tools if tool.name == "integral_query").defer_loading
    assert not build_brokered_tools(scope=_scope("empty"), catalogue=[])
