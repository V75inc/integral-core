"""Pydantic function tools remain wrappers around the Core broker."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic_ai import Agent, Tool
from pydantic_ai.capabilities import ToolSearch
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
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
async def test_integral_tools_require_search_capabilities_first(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Every operational tool invocation has a same-run discovery receipt."""
    invocations = []
    run_state: dict[str, Any] = {}
    catalogue = [
        {
            "name": "integral_list_tracks",
            "description": "List tracks in the current workspace.",
            "input_schema": {"type": "object", "properties": {}},
        }
    ]

    def infer(_name: str):
        return "core", "read"

    async def invoke(**kwargs: Any) -> CapabilityResult:
        invocations.append(kwargs)
        return CapabilityResult(ok=True, data={"tracks": []})

    monkeypatch.setattr(
        "app.agentive.services.capability_broker.infer_source_and_op_class", infer
    )
    monkeypatch.setattr(
        "app.agentive.services.capability_broker.invoke_declared_capability", invoke
    )
    tools = build_brokered_tools(
        scope=_scope(),
        catalogue=catalogue,
        skill_library=tmp_path,
        run_state=run_state,
    )
    by_name = {tool.name: tool for tool in tools}
    ctx = SimpleNamespace(tool_call_id="list-before-discovery")

    blocked = await by_name["integral_list_tracks"].function_schema.call({}, ctx)
    found = await by_name["search_capabilities"].function(
        query="which tracks are in my workspace"
    )
    listed = await by_name["integral_list_tracks"].function_schema.call(
        {}, SimpleNamespace(tool_call_id="list-after-discovery")
    )

    assert blocked["error_code"] == "capability_discovery_required"
    assert found["results"][0]["name"] == "integral_list_tracks"
    assert listed["tracks"] == []
    assert run_state["capability_search_completed"] is True
    assert len(invocations) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("policy", "expected_code"),
    [
        ({"no_workspace_writes": True}, "user_no_workspace_writes"),
        ({"design_only": True}, "design_proposal_only"),
    ],
)
async def test_host_turn_policies_are_enforced_by_native_broker(
    monkeypatch: pytest.MonkeyPatch,
    policy: dict[str, bool],
    expected_code: str,
) -> None:
    """Native turns enforce write barriers without process-local session state."""
    dispatched: list[dict[str, Any]] = []

    def infer(_name: str):
        return "core", "write"

    async def invoke(**kwargs: Any) -> CapabilityResult:
        dispatched.append(kwargs)
        return CapabilityResult(ok=True, data={"created": True})

    monkeypatch.setattr(
        "app.agentive.services.capability_broker.infer_source_and_op_class", infer
    )
    monkeypatch.setattr(
        "app.agentive.services.capability_broker.invoke_declared_capability", invoke
    )
    tool = build_brokered_tools(
        scope=_scope(),
        catalogue=[
            {
                "name": "integral_create_entry",
                "description": "Create an entry.",
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
        run_state={"capability_search_completed": True},
        **policy,
    )[0]

    result = await tool.function_schema.call(
        {}, SimpleNamespace(tool_call_id="policy-guarded-call")
    )

    assert result["error_code"] == expected_code
    assert result["retryable"] is False
    assert dispatched == []


@pytest.mark.asyncio
async def test_design_only_policy_allows_saved_design_proposal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Design-only blocks writes while preserving the intended proposal tool."""
    dispatched: list[dict[str, Any]] = []

    def infer(_name: str):
        return "core", "write"

    async def invoke(**kwargs: Any) -> CapabilityResult:
        dispatched.append(kwargs)
        return CapabilityResult(ok=True, data={"proposal": "Saved proposal"})

    monkeypatch.setattr(
        "app.agentive.services.capability_broker.infer_source_and_op_class", infer
    )
    monkeypatch.setattr(
        "app.agentive.services.capability_broker.invoke_declared_capability", invoke
    )
    tool = build_brokered_tools(
        scope=_scope(),
        catalogue=[
            {
                "name": "integral_propose_design",
                "description": "Save an app design proposal.",
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
        run_state={
            "capability_search_completed": True,
            "scaffold_coverage_validated": True,
        },
        design_only=True,
    )[0]

    result = await tool.function_schema.call(
        {},
        SimpleNamespace(
            tool_call_id="design-only-proposal",
            active_capability_ids={"integral-scaffold"},
        ),
    )

    assert result["proposal"] == "Saved proposal"
    assert len(dispatched) == 1


@pytest.mark.asyncio
async def test_preparation_hides_tools_until_search_and_required_skill_load(
    tmp_path: Path,
) -> None:
    """Pydantic AI sees lifecycle tools only when their prerequisites hold."""
    run_state: dict[str, Any] = {}
    tools = build_brokered_tools(
        scope=_scope(),
        catalogue=[
            {
                "name": "integral_propose_design",
                "description": "Save an app design proposal.",
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
        skill_library=tmp_path,
        run_state=run_state,
    )
    by_name = {tool.name: tool for tool in tools}
    propose = by_name["integral_propose_design"]
    ctx_without_skill = SimpleNamespace(active_capability_ids=set())

    assert await propose.prepare_tool_def(ctx_without_skill) is None
    await by_name["search_capabilities"].function(query="design an app")
    assert await propose.prepare_tool_def(ctx_without_skill) is None
    ctx_with_skill = SimpleNamespace(active_capability_ids={"integral-scaffold"})
    assert await propose.prepare_tool_def(ctx_with_skill) is None
    run_state["scaffold_coverage_validated"] = True
    available = await propose.prepare_tool_def(ctx_with_skill)

    assert available is not None


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
        run_state={"capability_search_completed": True},
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
    """Keep orientation and resident lifecycle tools visible for the model."""
    catalogue = build_tool_catalogue()
    tools = build_brokered_tools(
        scope=_scope(),
        catalogue=catalogue,
        run_state={"capability_search_completed": True},
    )
    by_name = {tool.name: tool for tool in tools}

    always_available = {
        "integral_get_scope",
        "integral_list_workspaces",
        "integral_list_apps",
        "integral_list_tracks",
        "integral_query_entries",
        "integral_check_design_coverage",
        "integral_propose_design",
        "integral_build_approved_design",
        "integral_verify_build",
    }
    assert {name for name, tool in by_name.items() if not tool.defer_loading} == (
        always_available
    )
    assert len(by_name) > 100


def test_scaffold_lifecycle_tools_are_directly_callable_after_skill_load() -> None:
    """A loaded skill can invoke its key lifecycle tools without another search."""
    catalogue = build_tool_catalogue()
    tools = build_brokered_tools(scope=_scope(), catalogue=catalogue)
    by_name = {tool.name: tool for tool in tools}
    assert by_name["integral_propose_design"].defer_loading is False
    assert by_name["integral_check_design_coverage"].defer_loading is False
    assert by_name["integral_build_approved_design"].defer_loading is False
    assert by_name["integral_verify_build"].defer_loading is False
    assert by_name["integral_get_scope"].defer_loading is False


@pytest.mark.asyncio
async def test_repeated_identical_read_is_suppressed_with_a_stop_instruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A repeated read cannot become an unbounded model/tool loop."""
    invocations = []

    def infer(_name: str):
        return "core", "read"

    async def invoke(**kwargs: Any) -> CapabilityResult:
        invocations.append(kwargs)
        return CapabilityResult(ok=True, data={"items": []})

    monkeypatch.setattr(
        "app.agentive.services.capability_broker.infer_source_and_op_class", infer
    )
    monkeypatch.setattr(
        "app.agentive.services.capability_broker.invoke_declared_capability", invoke
    )
    tool = build_brokered_tools(
        scope=_scope(),
        catalogue=[
            {
                "name": "integral_list_tracks",
                "description": "List tracks.",
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
        run_state={"capability_search_completed": True},
    )[0]

    first = await tool.function_schema.call({}, SimpleNamespace(tool_call_id="call-1"))
    second = await tool.function_schema.call({}, SimpleNamespace(tool_call_id="call-2"))

    assert first["items"] == []
    assert second["error_code"] == "repeated_read_suppressed"
    assert second["retryable"] is False
    assert "do not repeat" in second["message"]
    assert len(invocations) == 1


@pytest.mark.asyncio
async def test_provider_stringified_object_is_normalized_before_broker_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The native adapter repairs structured-argument encoding, not semantics."""
    invocations: list[dict[str, Any]] = []

    def infer(_name: str):
        return "core", "read"

    async def invoke(**kwargs: Any) -> CapabilityResult:
        invocations.append(kwargs)
        return CapabilityResult(
            ok=True, data={"status": "buildable", "unsupported": []}
        )

    monkeypatch.setattr(
        "app.agentive.services.capability_broker.infer_source_and_op_class", infer
    )
    monkeypatch.setattr(
        "app.agentive.services.capability_broker.invoke_declared_capability", invoke
    )
    tool = build_brokered_tools(
        scope=_scope(),
        catalogue=[
            {
                "name": "integral_check_design_coverage",
                "description": "Check a design blueprint.",
                "input_schema": {
                    "type": "object",
                    "properties": {"blueprint": {"type": "object"}},
                },
            }
        ],
        run_state={"capability_search_completed": True},
    )[0]

    result = await tool.function_schema.call(
        {"blueprint": '{"tracks": []}'},
        SimpleNamespace(
            tool_call_id="coverage-call",
            active_capability_ids={"integral-scaffold"},
        ),
    )

    assert result["status"] == "buildable"
    assert invocations[0]["arguments"] == {"blueprint": {"tracks": []}}


@pytest.mark.asyncio
async def test_scaffold_phase_calls_have_bounded_retry_budgets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validation and proposal retries stop after two useful attempts."""
    invocations = []

    def infer(_name: str):
        return "core", "write"

    async def invoke(**kwargs: Any) -> CapabilityResult:
        invocations.append(kwargs)
        if kwargs["capability_key"] == "integral_check_design_coverage":
            return CapabilityResult(
                ok=True, data={"status": "buildable", "unsupported": []}
            )
        return CapabilityResult(ok=True, data={"proposal_id": len(invocations)})

    monkeypatch.setattr(
        "app.agentive.services.capability_broker.infer_source_and_op_class", infer
    )
    monkeypatch.setattr(
        "app.agentive.services.capability_broker.invoke_declared_capability", invoke
    )
    tools = build_brokered_tools(
        scope=_scope(),
        catalogue=[
            {
                "name": "integral_check_design_coverage",
                "description": "Check a design blueprint.",
                "input_schema": {
                    "type": "object",
                    "properties": {"blueprint": {"type": "object"}},
                },
            },
            {
                "name": "integral_propose_design",
                "description": "Save a design proposal.",
                "input_schema": {
                    "type": "object",
                    "properties": {"proposal": {"type": "string"}},
                },
            },
        ],
        run_state={"capability_search_completed": True},
    )
    by_name = {tool.name: tool for tool in tools}
    await by_name["integral_check_design_coverage"].function_schema.call(
        {"blueprint": {}},
        SimpleNamespace(
            tool_call_id="coverage-call", active_capability_ids={"integral-scaffold"}
        ),
    )
    tool = by_name["integral_propose_design"]

    first = await tool.function_schema.call(
        {"proposal": "first"},
        SimpleNamespace(
            tool_call_id="call-1", active_capability_ids={"integral-scaffold"}
        ),
    )
    second = await tool.function_schema.call(
        {"proposal": "corrected"},
        SimpleNamespace(
            tool_call_id="call-2", active_capability_ids={"integral-scaffold"}
        ),
    )
    third = await tool.function_schema.call(
        {"proposal": "another correction"},
        SimpleNamespace(
            tool_call_id="call-3", active_capability_ids={"integral-scaffold"}
        ),
    )

    assert first["proposal_id"] == 2
    assert second["proposal_id"] == 3
    assert third["error_code"] == "capability_call_limit"
    assert third["retryable"] is False
    assert len(invocations) == 3


@pytest.mark.asyncio
async def test_coverage_allows_one_schema_correction_before_stopping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Coverage affords one correction and records only a successful check."""
    attempts = 0

    def infer(_name: str):
        return "core", "read"

    async def invoke(**kwargs: Any) -> CapabilityResult:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return CapabilityResult(
                ok=False,
                error_code="invalid_blueprint",
                message="Correct the blueprint schema.",
            )
        return CapabilityResult(
            ok=True, data={"status": "buildable", "unsupported": []}
        )

    monkeypatch.setattr(
        "app.agentive.services.capability_broker.infer_source_and_op_class", infer
    )
    monkeypatch.setattr(
        "app.agentive.services.capability_broker.invoke_declared_capability", invoke
    )
    tools = build_brokered_tools(
        scope=_scope(),
        catalogue=[
            {
                "name": "integral_check_design_coverage",
                "description": "Validate an app design blueprint.",
                "input_schema": {
                    "type": "object",
                    "properties": {"blueprint": {"type": "object"}},
                },
            }
        ],
        run_state={"capability_search_completed": True},
    )
    coverage = tools[0]

    def ctx(call_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            tool_call_id=call_id, active_capability_ids={"integral-scaffold"}
        )

    failed = await coverage.function_schema.call(
        {"blueprint": {"revision": 1}}, ctx("a")
    )
    corrected = await coverage.function_schema.call(
        {"blueprint": {"revision": 2}}, ctx("b")
    )
    valid = await coverage.function_schema.call(
        {"blueprint": {"revision": 3}}, ctx("c")
    )
    limited = await coverage.function_schema.call(
        {"blueprint": {"revision": 4}}, ctx("d")
    )

    assert failed["error_code"] == "invalid_blueprint"
    assert corrected["error_code"] == "invalid_blueprint"
    assert valid["status"] == "buildable"
    assert limited["error_code"] == "capability_call_limit"
    assert attempts == 3


@pytest.mark.asyncio
async def test_scaffold_lifecycle_tools_require_loaded_skill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scaffold actions cannot bypass their resident workflow guidance."""
    invocations = []

    def infer(_name: str):
        return "core", "write"

    async def invoke(**kwargs: Any) -> CapabilityResult:
        invocations.append(kwargs)
        if kwargs["capability_key"] == "integral_check_design_coverage":
            return CapabilityResult(
                ok=True, data={"status": "buildable", "unsupported": []}
            )
        return CapabilityResult(
            ok=True,
            data={"proposal_id": "proposal-1", "proposal": "recorded proposal"},
        )

    monkeypatch.setattr(
        "app.agentive.services.capability_broker.infer_source_and_op_class", infer
    )
    monkeypatch.setattr(
        "app.agentive.services.capability_broker.invoke_declared_capability", invoke
    )
    run_state = {"capability_search_completed": True}
    tools = build_brokered_tools(
        scope=_scope(),
        catalogue=[
            {
                "name": "integral_check_design_coverage",
                "description": "Check a design blueprint.",
                "input_schema": {
                    "type": "object",
                    "properties": {"blueprint": {"type": "object"}},
                },
            },
            {
                "name": "integral_propose_design",
                "description": "Save a design proposal.",
                "input_schema": {
                    "type": "object",
                    "properties": {"proposal": {"type": "string"}},
                },
            },
        ],
        run_state=run_state,
    )
    by_name = {tool.name: tool for tool in tools}
    tool = by_name["integral_propose_design"]

    blocked = await tool.function_schema.call(
        {"proposal": "design"},
        SimpleNamespace(tool_call_id="call-before-load", active_capability_ids=set()),
    )
    coverage_required = await tool.function_schema.call(
        {"proposal": "design"},
        SimpleNamespace(
            tool_call_id="call-after-load",
            active_capability_ids={"integral-scaffold"},
        ),
    )
    checked = await by_name["integral_check_design_coverage"].function_schema.call(
        {"blueprint": {}},
        SimpleNamespace(
            tool_call_id="coverage-after-load",
            active_capability_ids={"integral-scaffold"},
        ),
    )
    allowed = await tool.function_schema.call(
        {"proposal": "design"},
        SimpleNamespace(
            tool_call_id="call-after-coverage",
            active_capability_ids={"integral-scaffold"},
        ),
    )

    assert blocked["error_code"] == "required_skill_not_loaded"
    assert coverage_required["error_code"] == "design_coverage_required"
    assert checked["status"] == "buildable"
    assert allowed["proposal_id"] == "proposal-1"
    assert run_state["proposal_succeeded"] is True
    assert run_state["proposal_text"] == "recorded proposal"
    assert len(invocations) == 2


def test_broker_registers_search_capabilities_with_the_projected_skills(
    tmp_path: Path,
) -> None:
    """Each provider run exposes unified search over its authorized skill set."""
    tools = build_brokered_tools(
        scope=_scope(),
        catalogue=[
            {
                "name": "integral_create_entry",
                "description": "Create an entry in an existing track.",
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
        skill_library=tmp_path,
    )

    search_tool = next(tool for tool in tools if tool.name == "search_capabilities")

    assert search_tool.defer_loading is False


@pytest.mark.asyncio
async def test_initial_model_request_exposes_only_unblocked_tools() -> None:
    """Scaffold APIs wait until discovery and their owning skill are loaded."""
    catalogue = build_tool_catalogue()
    tools = build_brokered_tools(
        scope=_scope(),
        catalogue=catalogue,
        run_state={"capability_search_completed": True},
    )
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
    assert observed_tool_names[0] == {
        "integral_get_scope",
        "integral_list_workspaces",
        "integral_list_apps",
        "integral_list_tracks",
        "integral_query_entries",
        "search_tools",
    }


@pytest.mark.asyncio
async def test_harness_tool_search_discovers_a_deferred_tool_from_plain_wording() -> (
    None
):
    """Harness ToolSearch remains available for the wider deferred toolset."""
    requests: list[set[str]] = []
    entry_calls = 0

    def create_entry() -> str:
        nonlocal entry_calls
        entry_calls += 1
        return "created record"

    def respond(_messages: list[Any], info: Any) -> ModelResponse:
        visible = {tool.name for tool in info.function_tools}
        requests.append(visible)
        if len(requests) == 1:
            assert visible == {"search_tools"}
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="search_tools",
                        args={"queries": ["create an entry record"]},
                        tool_call_id="search-entry",
                    )
                ]
            )
        if len(requests) == 2:
            assert "integral_create_entry" in visible
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="integral_create_entry",
                        args={},
                        tool_call_id="create-entry",
                    )
                ]
            )
        return ModelResponse(parts=[TextPart(content="I created the record.")])

    agent = Agent(
        FunctionModel(respond),
        tools=[
            Tool(
                create_entry,
                name="integral_create_entry",
                description="Create an entry record in an existing track.",
                defer_loading=True,
            )
        ],
        capabilities=[ToolSearch(strategy="keywords", max_results=8)],
    )
    result = await agent.run("Add a record to my tracker.")

    assert result.output == "I created the record."
    assert entry_calls == 1
    assert requests[0] == {"search_tools"}
    assert "integral_create_entry" in requests[1]


@pytest.mark.asyncio
async def test_approved_build_macro_runs_at_most_once_per_model_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A repeated model call cannot dispatch an already attempted build."""
    invocations = []

    def infer(_name: str):
        return "core", "write"

    async def invoke(**kwargs: Any) -> CapabilityResult:
        invocations.append(kwargs)
        return CapabilityResult(ok=True, data={"applied": True})

    monkeypatch.setattr(
        "app.agentive.services.capability_broker.infer_source_and_op_class", infer
    )
    monkeypatch.setattr(
        "app.agentive.services.capability_broker.invoke_declared_capability", invoke
    )
    tool = build_brokered_tools(
        scope=_scope(),
        catalogue=[
            {
                "name": "integral_build_approved_design",
                "description": "Build the approved design.",
                "input_schema": {
                    "type": "object",
                    "properties": {"operations": {"type": "array"}},
                },
            }
        ],
        run_state={"capability_search_completed": True},
    )[0]

    first = await tool.function_schema.call(
        {"operations": [{"tool": "integral_create_app"}]},
        SimpleNamespace(
            tool_call_id="call-1", active_capability_ids={"integral-scaffold"}
        ),
    )
    second = await tool.function_schema.call(
        {"operations": [{"tool": "integral_create_app"}]},
        SimpleNamespace(
            tool_call_id="call-2", active_capability_ids={"integral-scaffold"}
        ),
    )

    assert first["applied"] is True
    assert second["error_code"] == "build_already_attempted"
    assert second["retryable"] is False
    assert len(invocations) == 1
