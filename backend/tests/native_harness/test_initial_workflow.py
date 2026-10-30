"""Light nominates metadata, while current Core and Pydantic own activation."""

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai_harness.step_persistence import InMemoryStepStore

from app.agentive.harness.broker_tools import build_brokered_tools
from app.agentive.harness.runtime import build_native_runtime
from app.schemas.capability_broker import CapabilityResult
from tests.native_harness.wp_06.test_provider_stream import _scope

pytestmark = [pytest.mark.asyncio]


@pytest.mark.parametrize("allowed", [True, False])
@pytest.mark.parametrize("catalogue_size", [1, 80])
async def test_selected_workflow_uses_two_model_requests_and_live_broker(
    tmp_path, monkeypatch, allowed, catalogue_size
):
    """An admitted workflow uses the native capability and live broker."""
    skill = "sample-workflow"
    name = "integral_sample_read"
    names = [name] + [f"integral_extra_read_{i}" for i in range(catalogue_size - 1)]
    folder = tmp_path / skill
    folder.mkdir()
    (folder / "SKILL.md").write_text(
        f"---\nname: {skill}\ndescription: Read a sample.\nallowed-tools: {' '.join(names)}\n---\nRead the sample."
    )
    calls = []

    async def invoke(**kw):
        calls.append(kw)
        return CapabilityResult(
            ok=allowed,
            data={"count": 12} if allowed else {},
            error_code="" if allowed else "insufficient_permissions",
        )

    monkeypatch.setattr(
        "app.agentive.services.capability_broker.invoke_declared_capability", invoke
    )
    monkeypatch.setattr(
        "app.agentive.services.capability_broker.infer_source_and_op_class",
        lambda name: ("core", "read"),
    )
    requests = []

    def respond(messages, info):
        requests.append(info)
        assert "Read the sample." in str(info.instructions)
        assert name in {tool.name for tool in info.function_tools}
        visible = {tool.name for tool in info.function_tools}
        assert len(set(names) & visible) <= 8
        # One oversized declaration must stay deferred without excluding the
        # smaller useful reads that follow it in the same broad procedure.
        if catalogue_size > 1:
            assert names[1] not in visible
            assert names[2] in visible
        if len(requests) == 1:
            return ModelResponse(parts=[ToolCallPart(name, {})])
        return ModelResponse(
            parts=[TextPart("Twelve records." if allowed else "The read was refused.")]
        )

    scope = _scope("initial-skill")
    tools = build_brokered_tools(
        scope=scope,
        skill_library=tmp_path,
        catalogue=[
            {
                "name": tool_name,
                "description": (
                    "Read sample" * 2000
                    if tool_name == "integral_extra_read_0"
                    else "Read sample"
                ),
                "input_schema": {"type": "object", "properties": {}},
            }
            for tool_name in names
        ],
    )
    agent, _ = build_native_runtime(
        model=FunctionModel(respond),
        instructions="Read the requested count.",
        tools=tools,
        scope=scope,
        agent_name="sample",
        skill_directories=[tmp_path],
        allowed_skill_names={skill},
        initial_skill=(skill, "Read the sample."),
        step_store_backend=InMemoryStepStore(),
    )
    await agent.run(
        "How many records?",
        run_id=scope.framework_run_id,
        conversation_id=scope.framework_conversation_id,
    )
    assert len(requests) == 2 and len(calls) == 1
    assert (
        calls[0]["principal_id"] == scope.principal_id
        and calls[0]["workspace_id"] == scope.workspace_id
    )


async def test_unapproved_initial_skill_is_rejected(tmp_path):
    """The host cannot activate a workflow outside its approved catalog."""
    with pytest.raises(ValueError, match="authorized skills"):
        build_native_runtime(
            model=FunctionModel(lambda m, i: ModelResponse(parts=[TextPart("No")])),
            instructions="Read.",
            tools=[],
            scope=_scope("bad-initial"),
            agent_name="sample",
            initial_skill=("unapproved", "Injected"),
            step_store_backend=InMemoryStepStore(),
        )


@pytest.mark.parametrize("allowed", [True, False])
async def test_track_schema_is_directly_available_but_live_broker_decides(
    tmp_path, monkeypatch, allowed
):
    """Avoid schema discovery rounds without turning disclosure into access."""
    name = "integral_get_track_schema"
    invocations = []

    async def invoke(**kwargs):
        invocations.append(kwargs)
        return CapabilityResult(
            ok=allowed,
            data={"fields": [{"name": "status"}]} if allowed else {},
            error_code="" if allowed else "insufficient_permissions",
        )

    monkeypatch.setattr(
        "app.agentive.services.capability_broker.invoke_declared_capability", invoke
    )
    monkeypatch.setattr(
        "app.agentive.services.capability_broker.infer_source_and_op_class",
        lambda _name: ("core", "read"),
    )
    requests = []

    def respond(messages, info):
        requests.append(info)
        assert name in {tool.name for tool in info.function_tools}
        if len(requests) == 1:
            return ModelResponse(parts=[ToolCallPart(name, {"track_id": "track-a"})])
        return ModelResponse(parts=[TextPart("Read" if allowed else "Access denied")])

    scope = _scope("direct-track-schema")
    agent, _ = build_native_runtime(
        model=FunctionModel(respond),
        instructions="Inspect the current track.",
        tools=build_brokered_tools(
            scope=scope,
            skill_library=tmp_path,
            catalogue=[
                {
                    "name": name,
                    "description": "Read the current Track schema.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"track_id": {"type": "string"}},
                        "required": ["track_id"],
                    },
                }
            ],
        ),
        scope=scope,
        agent_name="sample",
        step_store_backend=InMemoryStepStore(),
    )
    result = await agent.run(
        "What fields are in this track?",
        run_id=scope.framework_run_id,
        conversation_id=scope.framework_conversation_id,
    )
    assert result.output == ("Read" if allowed else "Access denied")
    assert len(requests) == 2 and len(invocations) == 1
    assert invocations[0]["workspace_id"] == scope.workspace_id


@pytest.mark.parametrize(
    "selection,expected",
    [("permitted", "permitted"), ("not-offered", None), (None, None)],
)
async def test_light_selection_must_be_an_exact_current_candidate(
    monkeypatch, selection, expected
):
    """Only current, exact routing candidates can activate a workflow."""
    from app.agentive.harness.adaptive import EscalatedTurn
    from app.services.chat_providers.pydantic_ai_provider import PydanticAIProvider
    from tests.native_harness.test_adaptive_context import setup_turn

    ctx, thread, _, _ = await setup_turn(monkeypatch, escalation=True)

    async def candidates(**kw):
        return [{"id": "permitted", "description": "Read current records."}]

    monkeypatch.setattr(
        "app.agentive.harness.skill_sources.routing_skill_candidates", candidates
    )
    monkeypatch.setattr(
        "app.services.chat_providers.pydantic_ai_provider.build_litellm_sdk_model",
        lambda **kw: FunctionModel(
            lambda m, i: ModelResponse(
                parts=[
                    ToolCallPart(
                        "request_operational_context", {"skill_id": selection}, "pick"
                    )
                ]
            )
        ),
    )
    result = await PydanticAIProvider()._try_light_turn(ctx, thread, "owner", None)
    assert isinstance(result, EscalatedTurn) and result.skill_id == expected


@pytest.mark.parametrize("size", [200, 32000])
async def test_routed_workflow_uses_native_loader_without_a_smaller_body_limit(
    tmp_path, size
):
    from app.agentive.harness.skill_sources import (
        load_selected_workflow,
        materialize_standard_skill_library,
    )

    body = "Read current records. " * (size // 20)
    sources = [("domain-read", "Read the domain.", body)]
    allowed = materialize_standard_skill_library(sources, root=tmp_path)
    selected = await load_selected_workflow(
        selected_key="domain-read",
        skill_sources=sources,
        skill_directory=tmp_path,
        allowed_skill_names=allowed,
    )
    assert selected is not None and selected[0] == "domain-read"
    assert body.strip() in selected[1]


@pytest.mark.parametrize(
    "invalid", ["not_allowed", "duplicate", "changed", "unoffered"]
)
async def test_routed_workflow_revalidates_current_catalog_and_body(tmp_path, invalid):
    from app.agentive.harness.skill_sources import (
        load_selected_workflow,
        materialize_standard_skill_library,
    )

    sources = [("domain-read", "Read the domain.", "Current procedure.")]
    allowed = materialize_standard_skill_library(sources, root=tmp_path)
    if invalid == "duplicate":
        sources = sources + sources
    if invalid == "changed":
        sources = [("domain-read", "Read the domain.", "Replaced procedure.")]
    selected = await load_selected_workflow(
        selected_key="unknown" if invalid == "unoffered" else "domain-read",
        skill_sources=sources,
        skill_directory=tmp_path,
        allowed_skill_names=() if invalid == "not_allowed" else allowed,
    )
    assert selected is None


async def test_routed_workflow_uses_collision_safe_native_identifier(tmp_path):
    from app.agentive.harness.skill_sources import (
        load_selected_workflow,
        materialize_standard_skill_library,
    )

    sources = [
        ("domain/read", "First.", "First procedure."),
        ("domain-read", "Second.", "Second procedure."),
    ]
    allowed = materialize_standard_skill_library(sources, root=tmp_path)
    selected = await load_selected_workflow(
        selected_key="domain-read",
        skill_sources=sources,
        skill_directory=tmp_path,
        allowed_skill_names=allowed,
    )
    assert selected is not None and selected[0] in allowed
    assert selected[0] != "domain-read"
    assert "Second procedure." in selected[1]
    assert "First procedure." not in selected[1]


@pytest.mark.parametrize("core_count", [19, 40])
async def test_light_receives_core_procedures_even_without_keyword_overlap(
    monkeypatch, core_count
):
    """Natural domain language must not hide the generic Core read workflow."""
    from app.agentive.harness.skill_sources import routing_skill_candidates

    monkeypatch.setattr(
        "app.agentive.services.agent_skills.list_core_skills",
        lambda: [
            {"key": f"workflow-{index}", "description": "Independent procedure."}
            for index in range(core_count)
        ],
    )
    checked = []

    async def callable_skills(caller, workspace, **kwargs):
        checked.append((workspace, kwargs))
        return []

    monkeypatch.setattr(
        "app.agentive.services.skill_registry.get_callable_skills", callable_skills
    )
    choices = await routing_skill_candidates(
        workspace_id="workspace",
        principal_id="user",
        focused_app_id=None,
        query="How many bags of rice do we have?",
    )
    assert {row["id"] for row in choices} == {
        *sorted(f"workflow-{index}" for index in range(core_count))[:24]
    }
    assert checked == [("workspace", {"user_id": "user", "active_apps_only": True})]
