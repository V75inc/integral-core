"""Pydantic function tools remain wrappers around the Core broker."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic_ai import Agent
from pydantic_ai.capabilities import ToolSearch
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel

from app.agentive.harness.broker_tools import build_brokered_tools
from app.agentive.harness.contracts import HarnessExecutionScope
from app.agentive.tooling.catalogue import build_tool_catalogue
from app.schemas.capability_broker import CapabilityResult


def _scope() -> HarnessExecutionScope:
    return HarnessExecutionScope(
        tenant_id="workspace-1",
        principal_id="user-1",
        workspace_id="workspace-1",
        thread_id="thread-1",
        session_id="session-1",
        run_id="run-1",
        permission_revision="permissions-1",
        capability_version="tools-1",
    )


@pytest.mark.asyncio
async def test_tools_preserve_manifest_schema_and_dispatch_with_trusted_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The model sees declared schemas while identity comes from Core scope."""
    invocations = []
    catalogue = [
        {
            "name": "integral_query_entries",
            "description": "Find entries.",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
        {
            "name": "integral_list_tracks",
            "description": "List tracks.",
            "input_schema": {"type": "object", "properties": {}},
        },
    ]

    def infer(name: str):
        return "core", "read" if name.endswith("entries") else "read"

    async def invoke(**kwargs: Any) -> CapabilityResult:
        invocations.append(kwargs)
        return CapabilityResult(ok=True, data={"capability": kwargs["capability_key"]})

    monkeypatch.setattr(
        "app.agentive.services.capability_broker.infer_source_and_op_class", infer
    )
    monkeypatch.setattr(
        "app.agentive.services.capability_broker.invoke_declared_capability", invoke
    )

    tools = build_brokered_tools(
        scope=_scope(),
        skill_tools_required=["integral_query_entries"],
        catalogue=catalogue,
    )
    by_name = {tool.name: tool for tool in tools}
    assert by_name["integral_list_tracks"].defer_loading is False
    assert by_name["integral_query_entries"].defer_loading is False
    assert (
        by_name["integral_query_entries"].function_schema.json_schema
        == catalogue[0]["input_schema"]
    )

    first = await by_name["integral_query_entries"].function_schema.call(
        {"query": "active"}, SimpleNamespace(tool_call_id="call-1")
    )
    second = await by_name["integral_list_tracks"].function_schema.call(
        {}, SimpleNamespace(tool_call_id="call-2")
    )

    assert first["capability"] == "integral_query_entries"
    assert second["capability"] == "integral_list_tracks"
    assert len(invocations) == 2
    assert invocations[0]["principal_id"] == "user-1"
    assert invocations[0]["workspace_id"] == "workspace-1"
    assert invocations[0]["run_id"] == "run-1"
    assert invocations[0]["session_id"] == "session-1"
    assert invocations[0]["arguments"] == {"query": "active"}
    assert invocations[0]["skill_tools_required"] == ["integral_query_entries"]
    assert invocations[0]["idempotency_key"] != invocations[1]["idempotency_key"]

    await Agent(TestModel(call_tools=["integral_query_entries"]), tools=tools).run(
        "query active entries"
    )
    assert len(invocations) == 3
    assert invocations[2]["capability_key"] == "integral_query_entries"


def test_large_broker_catalogue_defers_nonessential_tools() -> None:
    """Keep five routine tools visible and defer the rest for discovery."""
    catalogue = build_tool_catalogue()
    tools = build_brokered_tools(scope=_scope(), catalogue=catalogue)
    by_name = {tool.name: tool for tool in tools}

    always_available = {
        "integral_get_scope",
        "integral_list_workspaces",
        "integral_list_apps",
        "integral_list_tracks",
        "integral_query_entries",
    }
    assert {name for name, tool in by_name.items() if not tool.defer_loading} == (
        always_available
    )
    assert len(by_name) > 100


@pytest.mark.asyncio
async def test_initial_model_request_only_exposes_navigation_and_search_tools() -> None:
    """The model discovers other broker tools instead of carrying all schemas."""
    catalogue = build_tool_catalogue()
    tools = build_brokered_tools(scope=_scope(), catalogue=catalogue)
    observed_tool_names: list[set[str]] = []

    def respond(_messages: list[Any], info: Any) -> ModelResponse:
        observed_tool_names.append({tool.name for tool in info.function_tools})
        return ModelResponse(parts=[TextPart(content="ready")])

    agent = Agent(
        FunctionModel(respond),
        tools=tools,
        capabilities=[ToolSearch(strategy="keywords", max_results=8)],
    )
    result = await agent.run("What workspace is this and are there any tracks?")

    assert result.output == "ready"
    assert len(observed_tool_names) == 1
    assert observed_tool_names[0] <= {
        "integral_get_scope",
        "integral_list_workspaces",
        "integral_list_apps",
        "integral_list_tracks",
        "integral_query_entries",
        "search_tools",
    }
