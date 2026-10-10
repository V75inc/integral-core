"""Pydantic function tools remain wrappers around the Core broker."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic_ai import Agent, ModelRetry, Tool, UnexpectedModelBehavior
from pydantic_ai.capabilities import ToolSearch
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel

from app.agentive.harness.broker_tools import (
    _resource_links_for_model,
    build_brokered_tools,
)
from app.agentive.harness.contracts import HarnessExecutionScope
from app.agentive.harness.pydantic_ai_compat import IntegralToolDisclosure
from app.agentive.harness.runtime import build_native_runtime
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


def test_resource_urls_preserve_opaque_ids_and_the_original_receipt():
    """Model-facing route enrichment leaves broker receipts unchanged."""
    source = {
        "tracks": [{"id": "n.Track.example", "title": "Posts"}],
        "apps": [{"id": "n.WorkspaceApp.example", "name": "Discussion"}],
        "entries": [{"id": "n.Entry.example", "title": "Drill"}],
        "_receipt": {"run_id": "run-1"},
    }
    result = _resource_links_for_model(source)
    assert result["tracks"][0]["url"] == "/tracks/n.Track.example"
    assert result["apps"][0]["url"] == "/apps/n.WorkspaceApp.example"
    assert result["entries"][0]["url"] == "/entries/n.Entry.example"
    assert result["tracks"][0]["id"] == "n.Track.example"
    assert result["_receipt"] == source["_receipt"]
    assert "url" not in source["tracks"][0]


def test_governed_query_rows_get_canonical_urls_without_mutating_receipts():
    """Declared and generic queries use rows, not entries collections."""
    source = {
        "rows": [
            {"id": "n.Entry.example", "track_id": "n.Track.example"},
            {"id": "n.Track.example"},
            {"id": "n.WorkspaceApp.example"},
            {"count": 3},
            {"id": "external-resource"},
            "scalar-row",
        ],
        "_receipt": {"state": "complete", "run_id": "run-1"},
    }
    result = _resource_links_for_model(source)
    assert [row["url"] for row in result["rows"][:3]] == [
        "/tracks/n.Track.example?entry=n.Entry.example",
        "/tracks/n.Track.example",
        "/apps/n.WorkspaceApp.example",
    ]
    assert result["rows"][3:] == source["rows"][3:]
    assert result["_receipt"] == source["_receipt"]
    assert "url" not in source["rows"][0]


def test_app_query_items_receive_resource_links_without_mutating_receipts():
    """App-defined query output can use items rather than rows."""
    source = {
        "items": [{"id": "n.Entry.smoke", "track_id": "n.Track.smoke"}],
        "_receipt": {"state": "complete"},
    }
    result = _resource_links_for_model(source)
    assert result["items"][0]["url"] == "/tracks/n.Track.smoke?entry=n.Entry.smoke"
    assert "url" not in source["items"][0]
    assert result["_receipt"] == source["_receipt"]


def test_pending_write_resolution_tool_is_only_exposed_for_scoped_pending_items():
    """Expose the chat decision tool only when Core supplied pending items."""
    tools = build_brokered_tools(
        scope=_scope(),
        catalogue=[],
        pending_approval_tokens={"ref-123": "secret-token"},
    )
    tool = next(item for item in tools if item.name == "integral_resolve_pending_write")
    assert "secret-token" not in str(tool.function_schema.json_schema)
    assert (
        tool.function_schema.json_schema["properties"]["item_reference"]["type"]
        == "string"
    )

    without_pending = build_brokered_tools(scope=_scope(), catalogue=[])
    assert all(
        item.name != "integral_resolve_pending_write" for item in without_pending
    )


@pytest.mark.asyncio
async def test_protected_write_uses_declared_skill_relationship_before_live_broker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Discovery alone must not skip the applicable skill's filing procedure."""
    skill = tmp_path / "receipt-filing"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: receipt-filing\ndescription: File receipts.\n"
        "allowed-tools: integral_create_entry\n---\nVerify document identity first.\n"
    )
    invocations = []

    async def invoke(**kwargs):
        invocations.append(kwargs)
        return CapabilityResult(ok=True, data={"_kind": "staged_change"})

    monkeypatch.setattr(
        "app.agentive.services.capability_broker.infer_source_and_op_class",
        lambda _name: ("core", "propose"),
    )
    monkeypatch.setattr(
        "app.agentive.services.capability_broker.invoke_declared_capability", invoke
    )
    tools = build_brokered_tools(
        scope=_scope(),
        skill_library=tmp_path,
        catalogue=[
            {
                "name": "integral_create_entry",
                "description": "Create an entry.",
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
    )
    tool = next(t for t in tools if t.name == "integral_create_entry")
    assert tool.sequential is True
    denied = await tool.function_schema.call(
        {}, SimpleNamespace(tool_call_id="before", active_capability_ids=set())
    )
    assert denied["error_code"] == "required_skill_not_loaded"
    assert "receipt-filing" in denied["message"]
    assert invocations == []

    result = await tool.function_schema.call(
        {},
        SimpleNamespace(tool_call_id="after", active_capability_ids={"receipt-filing"}),
    )
    assert result["_kind"] == "staged_change"
    assert len(invocations) == 1
    assert invocations[0]["principal_id"] == "user-1"
    assert invocations[0]["workspace_id"] == "workspace-1"


@pytest.mark.asyncio
async def test_known_integral_tools_still_cross_live_broker_without_search(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Discovery is not authority; known tools still use the live broker."""
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

    listed = await by_name["integral_list_tracks"].function_schema.call({}, ctx)
    found = await by_name["search_capabilities"].function(
        query="which tracks are in my workspace"
    )
    assert found.return_value["results"][0]["name"] == "integral_list_tracks"
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
async def test_proposal_is_disclosed_after_skill_load_without_run_local_coverage(
    tmp_path: Path,
) -> None:
    """Core validates proposals, including amendments in a later model run."""
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
async def test_setup_is_exposed_as_one_build_contract_not_staging_primitives(tmp_path):
    """Native search cannot disclose implementation-only app creation APIs."""
    tools = build_brokered_tools(
        scope=_scope(), catalogue=build_tool_catalogue(), skill_library=tmp_path
    )
    by_name = {tool.name: tool for tool in tools}
    primitives = {
        "integral_create_app",
        "integral_create_app_track",
        "integral_register_track_template",
    }
    assert not primitives.intersection(by_name)
    assert "integral_build_approved_design" in by_name
    result = await by_name["search_capabilities"].function(
        query="create an app with a track"
    )
    assert not primitives.intersection(result.tools)
    assert not primitives.intersection(
        item["name"] for item in result.return_value["results"]
    )


@pytest.mark.asyncio
async def test_repeated_identical_read_remains_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Framework limits bound execution without suppressing legitimate reads."""
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
    assert second["items"] == []
    assert len(invocations) == 2


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
async def test_scaffold_phase_corrections_are_not_blocked_by_broker_quotas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Framework usage limits govern proposal corrections, not per-tool quotas."""
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
    assert third["proposal_id"] == 4
    assert len(invocations) == 4


@pytest.mark.asyncio
async def test_coverage_remains_retryable_after_failed_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed validation can be corrected within the framework execution budget."""
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
    assert limited["status"] == "buildable"
    assert attempts == 4


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
    without_precheck = await tool.function_schema.call(
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
    assert without_precheck["proposal_id"] == "proposal-1"
    assert checked["status"] == "buildable"
    assert allowed["proposal_id"] == "proposal-1"
    assert run_state["proposal_succeeded"] is True
    assert run_state["proposal_text"] == "recorded proposal"
    assert len(invocations) == 3


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
async def test_integral_tools_wait_for_capability_search(tmp_path: Path) -> None:
    """Only the unified search tool is available before this turn's search."""
    catalogue = [
        item
        for item in build_tool_catalogue()
        if item["name"] in {"integral_get_scope", "integral_query_entries"}
    ]
    tools = build_brokered_tools(
        scope=_scope(),
        catalogue=catalogue,
        skill_library=tmp_path,
    )
    observed_tool_names: list[set[str]] = []

    def respond(_messages: list[Any], info: Any) -> ModelResponse:
        visible = {tool.name for tool in info.function_tools}
        observed_tool_names.append(visible)
        if len(observed_tool_names) == 1:
            assert visible == {"search_capabilities"}
            return ModelResponse(
                parts=[ToolCallPart("search_capabilities", {"query": "find a record"})]
            )
        assert "integral_query_entries" in visible
        return ModelResponse(parts=[TextPart(content="ready")])

    agent = Agent(
        FunctionModel(respond),
        tools=tools,
        capabilities=[IntegralToolDisclosure(max_results=8)],
    )
    result = await agent.run("Find a record in my workspace.")

    assert result.output == "ready"
    assert len(observed_tool_names) == 2
    assert observed_tool_names[0] == {"search_capabilities"}


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
async def test_unified_search_discloses_tools_and_replays_native_availability(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One catalog call reveals a deferred schema, with no second search needed."""
    from pydantic_ai_harness.step_persistence import InMemoryStepStore

    invocations = []
    requests = []
    catalogue = [
        {
            "name": "integral_list_entries",
            "description": "List entry records in the current workspace.",
            "input_schema": {"type": "object", "properties": {}},
        }
    ]

    async def invoke(**kwargs: Any) -> CapabilityResult:
        invocations.append(kwargs)
        return CapabilityResult(ok=True, data={"entries": []})

    monkeypatch.setattr(
        "app.agentive.services.capability_broker.invoke_declared_capability", invoke
    )
    monkeypatch.setattr(
        "app.agentive.services.capability_broker.infer_source_and_op_class",
        lambda _name: ("core", "read"),
    )

    def respond(_messages: list[Any], info: Any) -> ModelResponse:
        visible = {tool.name for tool in info.function_tools}
        requests.append(visible)
        assert "search_tools" not in visible
        if len(requests) == 1:
            assert visible - {"search_conversation_history"} == {"search_capabilities"}
            return ModelResponse(
                parts=[
                    ToolCallPart("search_capabilities", {"query": "list entry records"})
                ]
            )
        if len(requests) in {2, 4}:
            assert "integral_list_entries" in visible
            return ModelResponse(parts=[ToolCallPart("integral_list_entries", {})])
        return ModelResponse(parts=[TextPart("No entries yet.")])

    store = InMemoryStepStore()

    def runtime(scope):
        return build_native_runtime(
            model=FunctionModel(respond),
            instructions="Use the authorized catalog when needed.",
            tools=build_brokered_tools(
                scope=scope, catalogue=catalogue, skill_library=tmp_path
            ),
            step_store_backend=store,
            scope=scope,
            agent_name="integral-test",
        )[0]

    scope = _scope()
    first = await runtime(scope).run(
        "What records do I have?",
        conversation_id=scope.framework_conversation_id,
        run_id=scope.framework_run_id,
    )
    assert first.output == "No entries yet."
    # Fresh factory: visibility is restored from framework history, not a
    # process-local selected-tool list or a duplicated discovery receipt.
    next_scope = scope.model_copy(update={"run_id": "run-2"})
    second = await runtime(next_scope).run(
        "Thanks.",
        message_history=first.all_messages(),
        conversation_id=next_scope.framework_conversation_id,
        run_id=next_scope.framework_run_id,
    )
    assert second.output == "No entries yet."
    assert len(requests) == 5
    assert len(invocations) == 2
    assert invocations[0]["workspace_id"] == _scope().workspace_id
    assert invocations[0]["principal_id"] == _scope().principal_id


@pytest.mark.asyncio
async def test_loading_a_standard_skill_reveals_its_declared_brokered_tools(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A loaded Agent Skill reveals its declared Core tools."""
    from pydantic_ai_harness.step_persistence import InMemoryStepStore

    from app.agentive.harness.skill_sources import materialize_standard_skill_library

    names = materialize_standard_skill_library(
        [
            (
                "integral-entries",
                "Create records in an existing track.",
                "Use integral_create_entry for the requested record.",
            )
        ],
        root=tmp_path,
        allowed_tools={"integral-entries": ["integral_create_entry"]},
    )
    calls = []

    async def invoke(**kwargs):
        calls.append(kwargs)
        return CapabilityResult(ok=True, data={"staged": True})

    monkeypatch.setattr(
        "app.agentive.services.capability_broker.invoke_declared_capability", invoke
    )
    requests = []

    def respond(_messages, info):
        visible = {tool.name for tool in info.function_tools}
        requests.append(visible)
        assert "search_tools" not in visible
        if len(requests) == 1:
            assert visible - {"search_conversation_history"} == {
                "search_capabilities",
                "load_capability",
            }
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "search_capabilities", {"query": "create a record in a track"}
                    )
                ]
            )
        if len(requests) == 2:
            assert "load_capability" in visible
            assert "integral_create_entry" not in visible
            return ModelResponse(
                parts=[ToolCallPart("load_capability", {"id": "integral-entries"})]
            )
        if len(requests) == 3:
            assert "integral_create_entry" in visible
            return ModelResponse(parts=[ToolCallPart("integral_create_entry", {})])
        return ModelResponse(parts=[TextPart("The requested record is staged.")])

    scope = _scope()
    catalogue = [
        {
            "name": "integral_create_entry",
            "description": "Create a record.",
            "input_schema": {"type": "object", "properties": {}},
        }
    ]
    agent, _ = build_native_runtime(
        model=FunctionModel(respond),
        instructions="Use the record workflow.",
        tools=build_brokered_tools(
            scope=scope, catalogue=catalogue, skill_library=tmp_path
        ),
        step_store_backend=InMemoryStepStore(),
        scope=scope,
        agent_name="integral-test",
        skill_directories=[tmp_path],
        allowed_skill_names=names,
    )
    result = await agent.run(
        "Add a drill.",
        conversation_id=scope.framework_conversation_id,
        run_id=scope.framework_run_id,
    )
    assert result.output == "The requested record is staged."
    assert len(calls) == 1
    assert calls[0]["principal_id"] == scope.principal_id


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
    run_state = {"capability_search_completed": True, "approved_design_ready": False}
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
        run_state=run_state,
    )[0]

    disclosed = await tool.prepare_tool_def(
        SimpleNamespace(active_capability_ids={"integral-scaffold"})
    )
    assert disclosed is not None
    rejected = await tool.function_schema.call(
        {"operations": []},
        SimpleNamespace(
            tool_call_id="premature-build", active_capability_ids={"integral-scaffold"}
        ),
    )
    assert rejected["error_code"] == "design_approval_required"
    assert invocations == []
    run_state["approved_design_ready"] = True
    resumed_disclosure = await tool.prepare_tool_def(
        SimpleNamespace(active_capability_ids=set())
    )
    assert resumed_disclosure is not None
    first = await tool.function_schema.call(
        {"operations": [{"tool": "integral_create_app"}]},
        SimpleNamespace(tool_call_id="call-1", active_capability_ids=set()),
    )
    second = await tool.function_schema.call(
        {"operations": [{"tool": "integral_create_app"}]},
        SimpleNamespace(tool_call_id="call-2", active_capability_ids=set()),
    )

    assert first["applied"] is True
    assert second["error_code"] == "build_already_attempted"
    assert second["retryable"] is False
    assert len(invocations) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code",
    [
        "invalid_scaffold_plan",
        "plan_differs_from_design",
        "scaffold_plan_validation_failed",
    ],
)
async def test_pre_effect_build_validation_can_be_corrected_with_framework_retry(
    monkeypatch, code
):
    calls = []

    async def invoke(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return CapabilityResult(
                ok=False,
                error_code=code,
                message=(
                    "The plan adds Track 'Vehicle Details', which is not in the "
                    "approved design."
                ),
            )
        return CapabilityResult(ok=True, data={"applied": True})

    monkeypatch.setattr(
        "app.agentive.services.capability_broker.invoke_declared_capability", invoke
    )
    monkeypatch.setattr(
        "app.agentive.services.capability_broker.infer_source_and_op_class",
        lambda _: ("core", "write"),
    )
    tool = build_brokered_tools(
        scope=_scope(),
        run_state={"approved_design_ready": True, "capability_search_completed": True},
        catalogue=[
            {
                "name": "integral_build_approved_design",
                "description": "Build",
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
    )[0]
    assert tool.max_retries == 2
    ctx = SimpleNamespace(tool_call_id="invalid", active_capability_ids=set())
    with pytest.raises(ModelRetry, match="approved design") as retry:
        await tool.function_schema.call({}, ctx)
    assert "Do not ask the user to repeat approval" in str(retry.value)
    ctx.tool_call_id = "corrected"
    assert (await tool.function_schema.call({}, ctx))["applied"] is True
    ctx.tool_call_id = "duplicate"
    assert (await tool.function_schema.call({}, ctx))[
        "error_code"
    ] == "build_already_attempted"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_framework_bounds_pre_effect_build_correction(monkeypatch):
    calls = []

    async def invoke(**kwargs):
        calls.append(kwargs)
        return CapabilityResult(
            ok=False,
            error_code="scaffold_plan_validation_failed",
            message="Correct invalid arguments.",
        )

    monkeypatch.setattr(
        "app.agentive.services.capability_broker.invoke_declared_capability", invoke
    )
    monkeypatch.setattr(
        "app.agentive.services.capability_broker.infer_source_and_op_class",
        lambda _: ("core", "write"),
    )
    tools = build_brokered_tools(
        scope=_scope(),
        run_state={"approved_design_ready": True, "capability_search_completed": True},
        catalogue=[
            {
                "name": "integral_build_approved_design",
                "description": "Build",
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
    )
    agent = Agent(
        FunctionModel(
            lambda _messages, _info: ModelResponse(
                parts=[ToolCallPart("integral_build_approved_design", {})]
            )
        ),
        tools=tools,
    )
    with pytest.raises(UnexpectedModelBehavior, match="retries"):
        await agent.run("Build the approved design.")
    assert len(calls) == 3  # Initial validation plus the two library-owned retries.


@pytest.mark.asyncio
async def test_plan_drift_is_repaired_by_pydantic_in_the_same_turn(
    monkeypatch, tmp_path
):
    """An extra generated Track is corrected without repeating user approval."""
    from pydantic_ai_harness.step_persistence import InMemoryStepStore

    invocations = []

    async def invoke(**kwargs):
        invocations.append(kwargs)
        if len(invocations) == 1:
            return CapabilityResult(
                ok=False,
                error_code="plan_differs_from_design",
                message=(
                    "The plan adds Track 'Vehicle Details', which is not in the "
                    "approved design."
                ),
            )
        return CapabilityResult(ok=True, data={"applied": True})

    monkeypatch.setattr(
        "app.agentive.services.capability_broker.invoke_declared_capability", invoke
    )
    monkeypatch.setattr(
        "app.agentive.services.capability_broker.infer_source_and_op_class",
        lambda _: ("core", "write"),
    )
    model_calls = []

    def respond(_messages, _info):
        model_calls.append(len(model_calls) + 1)
        if len(model_calls) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "integral_build_approved_design",
                        {
                            "operations": [
                                {
                                    "tool": "integral_create_app_track",
                                    "args": {"name": "Vehicle Details"},
                                }
                            ]
                        },
                    )
                ]
            )
        if len(model_calls) == 2:
            return ModelResponse(
                parts=[
                    ToolCallPart("integral_build_approved_design", {"operations": []})
                ]
            )
        return ModelResponse(
            parts=[TextPart("The approved setup is built and ready to review.")]
        )

    scope = _scope()
    agent, _ = build_native_runtime(
        model=FunctionModel(respond),
        instructions="Continue the saved approved design.",
        tools=build_brokered_tools(
            scope=scope,
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
            run_state={"approved_design_ready": True},
        ),
        step_store_backend=InMemoryStepStore(),
        scope=scope,
        agent_name="scaffold-plan-retry-test",
    )

    result = await agent.run(
        "Build the approved design.",
        conversation_id=scope.framework_conversation_id,
        run_id=scope.framework_run_id,
    )

    assert result.output == "The approved setup is built and ready to review."
    assert len(model_calls) == 3
    assert len(invocations) == 2


@pytest.mark.asyncio
async def test_saved_build_continuation_is_visible_before_new_catalog_search(
    monkeypatch, tmp_path
):
    from pydantic_ai_harness.step_persistence import InMemoryStepStore

    calls = []

    async def invoke(**kwargs):
        calls.append(kwargs)
        return CapabilityResult(ok=True, data={"applied": True})

    monkeypatch.setattr(
        "app.agentive.services.capability_broker.invoke_declared_capability", invoke
    )
    monkeypatch.setattr(
        "app.agentive.services.capability_broker.infer_source_and_op_class",
        lambda _: ("core", "write"),
    )
    requests = []

    def respond(_messages, info):
        requests.append({tool.name for tool in info.function_tools})
        if len(requests) == 1:
            assert "integral_build_approved_design" in requests[-1]
            return ModelResponse(
                parts=[ToolCallPart("integral_build_approved_design", {})]
            )
        return ModelResponse(parts=[TextPart("Built.")])

    scope = _scope()
    tools = build_brokered_tools(
        scope=scope,
        skill_library=tmp_path,
        run_state={"approved_design_ready": True},
        catalogue=[
            {
                "name": "integral_build_approved_design",
                "description": "Build",
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
    )
    agent, _ = build_native_runtime(
        model=FunctionModel(respond),
        instructions="Continue the approved design.",
        tools=tools,
        step_store_backend=InMemoryStepStore(),
        scope=scope,
        agent_name="continuation-test",
    )
    result = await agent.run(
        "Build it.",
        conversation_id=scope.framework_conversation_id,
        run_id=scope.framework_run_id,
    )
    assert result.output == "Built."
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code", ["batch_partial_failure", "forbidden", "execution_failed"]
)
async def test_effect_or_authority_failure_does_not_allow_build_replay(
    monkeypatch, code
):
    calls = []

    async def invoke(**kwargs):
        calls.append(kwargs)
        return CapabilityResult(ok=False, error_code=code, message="Do not replay.")

    monkeypatch.setattr(
        "app.agentive.services.capability_broker.invoke_declared_capability", invoke
    )
    monkeypatch.setattr(
        "app.agentive.services.capability_broker.infer_source_and_op_class",
        lambda _: ("core", "write"),
    )
    tool = build_brokered_tools(
        scope=_scope(),
        run_state={"approved_design_ready": True, "capability_search_completed": True},
        catalogue=[
            {
                "name": "integral_build_approved_design",
                "description": "Build",
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
    )[0]
    ctx = SimpleNamespace(tool_call_id="first", active_capability_ids=set())
    assert (await tool.function_schema.call({}, ctx))["error_code"] == code
    ctx.tool_call_id = "repeated"
    assert (await tool.function_schema.call({}, ctx))[
        "error_code"
    ] == "build_already_attempted"
    assert len(calls) == 1


def test_approved_saved_build_is_a_known_tool_in_its_resume_turn(
    tmp_path: Path,
) -> None:
    """A verified approval discloses only the build continuation in a new turn."""
    tools = build_brokered_tools(
        scope=_scope(),
        catalogue=[
            {
                "name": "integral_build_approved_design",
                "description": "Build the saved approved design.",
                "input_schema": {
                    "type": "object",
                    "properties": {"operations": {"type": "array"}},
                },
            },
            {
                "name": "integral_list_apps",
                "description": "List apps.",
                "input_schema": {"type": "object", "properties": {}},
            },
        ],
        skill_library=tmp_path,
        run_state={
            "approved_design_ready": True,
            "capability_search_completed": True,
        },
    )
    build = next(
        tool for tool in tools if tool.name == "integral_build_approved_design"
    )
    unrelated = next(tool for tool in tools if tool.name == "integral_list_apps")

    assert build.defer_loading is False
    assert unrelated.defer_loading is True


@pytest.mark.asyncio
async def test_pending_design_build_is_disclosed_without_fresh_turn_search(
    tmp_path: Path,
) -> None:
    """A fresh user reply can select the saved-design build without skill rediscovery."""
    run_state = {
        "pending_design": True,
        "approved_design_ready": False,
        "capability_search_completed": False,
    }
    tools = build_brokered_tools(
        scope=_scope(),
        catalogue=[
            {
                "name": "integral_build_approved_design",
                "description": "Build the saved design after user approval.",
                "input_schema": {
                    "type": "object",
                    "properties": {"operations": {"type": "array"}},
                },
            },
            {
                "name": "integral_list_apps",
                "description": "List apps.",
                "input_schema": {"type": "object", "properties": {}},
            },
        ],
        skill_library=tmp_path,
        run_state=run_state,
    )
    build = next(
        tool for tool in tools if tool.name == "integral_build_approved_design"
    )
    unrelated = next(tool for tool in tools if tool.name == "integral_list_apps")

    assert build.defer_loading is False
    assert unrelated.defer_loading is True
    assert (
        await build.prepare_tool_def(SimpleNamespace(active_capability_ids=set()))
        is not None
    )

    # The schema remains deferred. Pydantic AI owns whether it is revealed;
    # absence of a current-turn search is not a second authorization boundary.
    deferred = await unrelated.prepare_tool_def(
        SimpleNamespace(active_capability_ids=set())
    )
    assert deferred is not None and deferred.defer_loading

    run_state["capability_search_completed"] = True
    disclosed = await build.prepare_tool_def(
        SimpleNamespace(active_capability_ids=set())
    )
    assert disclosed is not None
    assert disclosed.name == "integral_build_approved_design"


@pytest.mark.asyncio
async def test_verbal_decisions_use_conversation_identity_not_checkpoint_session(
    monkeypatch,
):
    calls = []

    async def decide(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "decision": "approve", "state": "consumed"}

    monkeypatch.setattr(
        "app.agentive.services.approval_decisions.decide_staged_write", decide
    )
    tools = build_brokered_tools(
        scope=_scope(),
        catalogue=[],
        conversation_id="conversation-1",
        pending_approval_tokens={"ref": "proposal-token"},
    )
    resolver = next(
        tool for tool in tools if tool.name == "integral_resolve_pending_write"
    )
    result = await resolver.function_schema.call(
        {"item_reference": "ref", "decision": "approve"}, SimpleNamespace()
    )
    assert result["applied"] is True
    assert calls[0]["conversation_id"] == "conversation-1"
    assert calls[0]["workspace_id"] == "workspace-1"
    held = await resolver.function_schema.call(
        {"item_reference": "ref", "decision": "no_decision"}, SimpleNamespace()
    )
    assert held["applied"] is False
    assert len(calls) == 1
    guarded = build_brokered_tools(
        scope=_scope(),
        catalogue=[],
        no_workspace_writes=True,
        pending_approval_tokens={"ref": "proposal-token"},
    )
    resolver = next(
        tool for tool in guarded if tool.name == "integral_resolve_pending_write"
    )
    denied = await resolver.function_schema.call(
        {"item_reference": "ref", "decision": "approve"}, SimpleNamespace()
    )
    assert denied["error_code"] == "user_no_workspace_writes"
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("transient_failure", [False, True])
async def test_identical_read_recovers_and_observes_new_state(
    monkeypatch, transient_failure
):
    records = []
    attempts = 0

    async def invoke(**kwargs):
        nonlocal attempts
        attempts += 1
        if transient_failure and attempts == 1:
            return CapabilityResult(
                ok=False,
                error_code="temporarily_unavailable",
                message="Retry this read.",
            )
        return CapabilityResult(ok=True, data={"entries": list(records)})

    monkeypatch.setattr(
        "app.agentive.services.capability_broker.infer_source_and_op_class",
        lambda _name: ("core", "read"),
    )
    monkeypatch.setattr(
        "app.agentive.services.capability_broker.invoke_declared_capability", invoke
    )
    tool = build_brokered_tools(
        scope=_scope(),
        catalogue=[
            {
                "name": "integral_query_entries",
                "description": "Query entries.",
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
    )[0]
    first = await tool.function_schema.call({}, SimpleNamespace(tool_call_id="first"))
    if transient_failure:
        assert first["error_code"] == "temporarily_unavailable"
    else:
        assert first["entries"] == []
    records.append({"id": "n.Entry.created"})
    second = await tool.function_schema.call({}, SimpleNamespace(tool_call_id="second"))
    assert second["entries"][0]["id"] == "n.Entry.created"
    assert attempts == 2


@pytest.mark.asyncio
async def test_vetted_connector_read_crosses_broker_on_no_save_turn(monkeypatch):
    invocations = []

    async def invoke(**kwargs):
        invocations.append(kwargs)
        return CapabilityResult(ok=True, data={"results": []})

    monkeypatch.setattr(
        "app.agentive.services.capability_broker.invoke_declared_capability", invoke
    )
    tools = build_brokered_tools(
        scope=_scope(),
        no_workspace_writes=True,
        catalogue=[
            {
                "name": "mcp__serper_web_search__search_web",
                "description": "Search public sources.",
                "op_class": "read",
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                },
            },
            {
                "name": "mcp__custom__delete",
                "description": "Unknown remote side effects.",
                "input_schema": {"type": "object", "properties": {}},
            },
        ],
    )
    context = SimpleNamespace(tool_call_id="read", active_capability_ids=set())
    read = next(tool for tool in tools if tool.name.endswith("search_web"))
    assert read.sequential is False
    result = await read.function_schema.call({"query": "public documentation"}, context)
    assert result["results"] == []
    assert invocations[0]["source"] == "connector"
    assert invocations[0]["op_class"] == "read"
    assert invocations[0]["workspace_id"] == "workspace-1"
    write = next(tool for tool in tools if tool.name.endswith("delete"))
    assert write.sequential is True
    denied = await write.function_schema.call({}, context)
    assert denied["error_code"] == "user_no_workspace_writes"
    assert len(invocations) == 1


@pytest.mark.asyncio
async def test_durable_attachment_tool_checks_accepted_input_before_dispatch(
    monkeypatch,
):
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock

    from app.schemas.agentive.work import WorkError, WorkExecutionContext

    scope = _scope()
    work = WorkExecutionContext(
        work_item_id="work-1",
        attempt=1,
        run_id=scope.run_id,
        principal_id=scope.principal_id,
        workspace_id=scope.workspace_id,
        thread_id=scope.thread_id,
        logical_step_key="attachment-read",
        effect_key="effect-1",
        lease_token="lease-1",
        lease_fence=1,
    )
    item = SimpleNamespace(kind="chat_turn")
    invocations = []

    @asynccontextmanager
    async def authorized(context):
        assert context is work
        yield

    async def invoke(**kwargs):
        invocations.append(kwargs)
        return CapabilityResult(ok=True, data={"text": "accepted file"})

    guard = AsyncMock(side_effect=WorkError("work.policy_denied", "attachment changed"))
    monkeypatch.setattr(
        "app.agentive.services.work_items.authorized_work_item_effect", authorized
    )
    monkeypatch.setattr(
        "app.agentive.work_models.WorkItem.get", AsyncMock(return_value=item)
    )
    monkeypatch.setattr(
        "app.services.chat_turn_attachments.assert_chat_attachment_read_inputs", guard
    )
    monkeypatch.setattr(
        "app.agentive.services.capability_broker.infer_source_and_op_class",
        lambda name: ("core", "read"),
    )
    monkeypatch.setattr(
        "app.agentive.services.capability_broker.invoke_declared_capability", invoke
    )
    tools = build_brokered_tools(
        scope=scope,
        work_execution_context=work,
        catalogue=[
            {
                "name": "integral_get_attachment_text",
                "description": "Read a file.",
                "input_schema": {
                    "type": "object",
                    "properties": {"attachment_id": {"type": "string"}},
                },
            }
        ],
        run_state={"capability_search_completed": True},
    )
    tool = next(tool for tool in tools if tool.name == "integral_get_attachment_text")
    ctx = SimpleNamespace(tool_call_id="file-call", active_capability_ids=set())
    with pytest.raises(WorkError, match="attachment changed"):
        await tool.function_schema.call({"attachment_id": "file-1"}, ctx)
    assert invocations == []
    guard.assert_awaited_once_with(item=item)
    guard.side_effect = None
    result = await tool.function_schema.call({"attachment_id": "file-1"}, ctx)
    assert result["text"] == "accepted file"
    assert len(invocations) == 1


@pytest.mark.asyncio
async def test_durable_tools_forward_distinct_host_owned_logical_contexts(monkeypatch):
    from contextlib import asynccontextmanager

    from app.schemas.agentive.work import WorkError, WorkExecutionContext

    scope = _scope()
    parent = WorkExecutionContext(
        work_item_id="work-1",
        attempt=1,
        run_id=scope.run_id,
        principal_id=scope.principal_id,
        workspace_id=scope.workspace_id,
        thread_id=scope.thread_id,
        logical_step_key="provider:0",
        effect_key="parent-effect",
        lease_token="lease-1",
        lease_fence=1,
    )
    calls = []

    @asynccontextmanager
    async def authorized(context):
        assert context is parent
        yield

    async def invoke(**kwargs):
        calls.append(kwargs)
        return CapabilityResult(ok=True, data={"tracks": []})

    monkeypatch.setattr(
        "app.agentive.services.work_items.authorized_work_item_effect", authorized
    )
    monkeypatch.setattr(
        "app.agentive.services.capability_broker.infer_source_and_op_class",
        lambda name: ("core", "read"),
    )
    monkeypatch.setattr(
        "app.agentive.services.capability_broker.invoke_declared_capability", invoke
    )
    tools = build_brokered_tools(
        scope=scope,
        work_execution_context=parent,
        catalogue=[
            {
                "name": "integral_list_tracks",
                "description": "List tracks.",
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
    )
    tool = next(t for t in tools if t.name == "integral_list_tracks")
    for identifier in ("call-a", "call-b", "call-a"):
        await tool.function_schema.call(
            {}, SimpleNamespace(tool_call_id=identifier, active_capability_ids=set())
        )
    first, second, replay = [c["work_execution_context"] for c in calls]
    assert first.logical_step_key != second.logical_step_key
    assert first.effect_key != second.effect_key
    assert first == replay
    assert first.logical_step_key != parent.logical_step_key
    for context in (first, second):
        for field in (
            "work_item_id",
            "attempt",
            "run_id",
            "principal_id",
            "workspace_id",
            "thread_id",
            "lease_token",
            "lease_fence",
            "deadline_at",
            "cancellation_signal",
        ):
            assert getattr(context, field) == getattr(parent, field)
    with pytest.raises(WorkError, match="tool call identity"):
        await tool.function_schema.call(
            {}, SimpleNamespace(tool_call_id=None, active_capability_ids=set())
        )
    assert len(calls) == 3


def test_tool_effect_identity_survives_new_attempt_but_keeps_current_lease():
    from app.agentive.harness.broker_tools import _tool_work_context
    from app.schemas.agentive.work import WorkExecutionContext

    parent = WorkExecutionContext(
        work_item_id="work-1",
        attempt=1,
        run_id="run-1",
        principal_id="user-1",
        workspace_id="workspace-1",
        thread_id="thread-1",
        logical_step_key="provider:0",
        effect_key="parent-effect",
        lease_token="lease-1",
        lease_fence=1,
    )
    newer = parent.model_copy(
        update={
            "attempt": 2,
            "run_id": "run-2",
            "lease_token": "lease-2",
            "lease_fence": 2,
        }
    )
    first = _tool_work_context(parent, "persisted-call", "integral_list_tracks")
    replay = _tool_work_context(newer, "persisted-call", "integral_list_tracks")
    assert first.logical_step_key == replay.logical_step_key
    assert first.effect_key == replay.effect_key
    assert replay.attempt == 2 and replay.lease_fence == 2
    assert replay.run_id == "run-2" and replay.lease_token == "lease-2"
