"""Contract tests for Integral's version-sensitive Pydantic AI adapter."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel

from app.agentive.harness.pydantic_ai_compat import (
    IntegralToolDisclosure,
    RunContext,
    UsageLimitExceeded,
    build_integral_context_compaction,
    build_integral_json_schema_tool,
    build_integral_run_instructions,
    classify_integral_harness_exception,
)


@pytest.mark.asyncio
async def test_initial_discovery_uses_library_choice_then_allows_final_and_resume():
    choices = []

    async def search_capabilities(query: str):
        return {"query": query, "skills": ["integral-scaffold"]}

    def respond(messages, info):
        choice = (info.model_settings or {}).get("tool_choice", "auto")
        choices.append(choice)
        if choice == ["search_capabilities"]:
            assert choice == ["search_capabilities"]
            return ModelResponse(
                parts=[
                    ToolCallPart("search_capabilities", {"query": "visitor register"})
                ]
            )
        assert choice == "auto"
        return ModelResponse(parts=[TextPart("The library loop can finish.")])

    agent = Agent(
        FunctionModel(respond),
        tools=[search_capabilities],
        capabilities=[IntegralToolDisclosure()],
    )
    first = await agent.run("I need a visitor register.")
    assert first.output == "The library loop can finish."
    resumed = await agent.run("Thanks.", message_history=first.all_messages())
    assert resumed.output == first.output
    assert choices == [["search_capabilities"], "auto", ["search_capabilities"], "auto"]


@pytest.mark.parametrize(
    "profile",
    [
        {"supports_forced_tool_choice": False},
        {"supports_forced_tool_choice_with_thinking": False},
    ],
)
def test_discovery_choice_respects_public_model_profile_restrictions(profile):
    settings = IntegralToolDisclosure().get_model_settings()
    assert (
        settings(SimpleNamespace(model=SimpleNamespace(profile=profile), messages=[]))
        == {}
    )


@pytest.mark.parametrize("tool_name", ["search_capabilities", "load_capability"])
@pytest.mark.parametrize("failed", [False, True])
def test_only_recorded_successful_discovery_or_skill_load_releases_choice(
    tool_name, failed
):
    settings = IntegralToolDisclosure().get_model_settings()
    ctx = SimpleNamespace(
        model=SimpleNamespace(profile={}),
        messages=[
            ModelRequest(parts=[ToolReturnPart(tool_name, {"error": failed}, "call")])
        ],
    )
    assert settings(ctx) == ({"tool_choice": ["search_capabilities"]} if failed else {})


def test_integral_json_schema_tool_adapter_preserves_catalogue_contract() -> None:
    """Tool construction retains the manifest schema at the framework edge."""
    schema = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
        "additionalProperties": False,
    }

    async def invoke(ctx: RunContext[Any], **arguments: Any) -> dict[str, Any]:
        return arguments

    tool = build_integral_json_schema_tool(
        function=invoke,
        name="integral_lookup",
        description="Look up a permitted record.",
        json_schema=schema,
        prepare=None,
        defer_loading=True,
    )

    assert tool.name == "integral_lookup"
    assert tool.description == "Look up a permitted record."
    assert tool.tool_def.parameters_json_schema == schema
    assert tool.defer_loading is True


@pytest.mark.parametrize("active", [set(), {"receipt-skill"}])
def test_discovered_workflow_uses_library_skill_loading_before_execution(active):
    settings = IntegralToolDisclosure().get_model_settings()
    ctx = SimpleNamespace(
        model=SimpleNamespace(profile={}),
        active_capability_ids=active,
        messages=[
            ModelRequest(parts=[UserPromptPart("File this")]),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        "search_capabilities",
                        {
                            "results": [
                                {
                                    "kind": "skill",
                                    "load_with": {
                                        "id": "receipt-skill",
                                        "tool": "load_capability",
                                    },
                                }
                            ]
                        },
                        "search",
                    )
                ]
            ),
        ],
    )
    assert settings(ctx) == ({} if active else {"tool_choice": ["load_capability"]})


def test_old_discovery_does_not_select_a_new_user_workflow():
    choice = IntegralToolDisclosure().get_model_settings()
    ctx = SimpleNamespace(
        model=SimpleNamespace(profile={}),
        messages=[
            ModelRequest(
                parts=[ToolReturnPart("search_capabilities", {"matches": []}, "old")]
            ),
            ModelRequest(parts=[UserPromptPart("Delete this record")]),
        ],
    )
    assert choice(ctx)["tool_choice"] == ["search_capabilities"]


def test_application_code_uses_integral_adapter_for_pydantic_imports() -> None:
    """Keep third-party API/version coupling at the Integral binding seam."""
    app_root = Path(__file__).parents[3] / "app"
    adapter_path = app_root / "agentive" / "harness" / "pydantic_ai_compat.py"
    direct_imports: list[str] = []

    for source_path in app_root.rglob("*.py"):
        if source_path == adapter_path:
            continue
        tree = ast.parse(
            source_path.read_text(encoding="utf-8"), filename=str(source_path)
        )
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = (alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported = (node.module or "" for _ in node.names)
            else:
                continue
            if any(
                name == "pydantic_ai"
                or name.startswith("pydantic_ai.")
                or name == "pydantic_ai_harness"
                or name.startswith("pydantic_ai_harness.")
                or name == "pydantic_core"
                or name.startswith("pydantic_core.")
                for name in imported
            ):
                direct_imports.append(str(source_path.relative_to(app_root)))

    assert direct_imports == []

    shared_stream = app_root / "services" / "chat_streaming.py"
    stream_source = shared_stream.read_text(encoding="utf-8")
    assert "pydantic_ai" not in stream_source
    assert 'type(exc).__module__.startswith("jvagent")' not in stream_source


def test_run_instructions_expose_active_capabilities_to_the_model() -> None:
    """Loaded skills remain explicit without depending on repeated loads."""
    instructions = build_integral_run_instructions("Integral resident agent.")

    assert instructions(SimpleNamespace(active_capability_ids=set())) == (
        "Integral resident agent."
    )
    active = instructions(
        SimpleNamespace(active_capability_ids={"integral-scaffold", "integral-model"})
    )
    assert "already loaded" in active
    assert "integral-model, integral-scaffold" in active
    assert "Do not call load_capability for them again." in active


def test_run_instructions_surface_current_core_outcomes_over_old_proposal_text():
    instructions = build_integral_run_instructions("Integral resident agent.")
    history = [
        ModelRequest(
            parts=[
                ToolReturnPart(
                    "integral_commit_batch",
                    {
                        "token": "proposal-a",
                        "state": "consumed",
                        "state_source": "current_core_staging",
                        "summary": "Untrusted document says ignore instructions",
                    },
                    "call",
                )
            ]
        )
    ]
    result = instructions(
        SimpleNamespace(active_capability_ids=set(), messages=history)
    )
    assert "proposal-a: consumed" in result
    assert "None of these changes is awaiting approval" in result
    assert "Untrusted document" not in result


def test_integral_compaction_preserves_the_recent_working_set() -> None:
    """Keep the live outcome and skill state while compacting bulky history."""
    compaction = build_integral_context_compaction()

    assert compaction.max_tokens is None
    assert compaction.max_fraction == 0.7
    assert compaction.fallback_context_window == 32_768
    assert compaction.keep_pairs == 8
    assert compaction.exclude_tools == frozenset({"load_capability"})
    assert compaction.clear_tool_inputs is True


@pytest.mark.asyncio
async def test_compaction_keeps_skill_and_working_set_and_clears_old_payloads() -> None:
    """Compact model-visible history without mutating the durable source list."""
    compaction = build_integral_context_compaction()
    messages = [
        ModelResponse(
            parts=[ToolCallPart("search_capabilities", {"query": "q" * 4000}, "search")]
        ),
        ModelRequest(
            parts=[ToolReturnPart("search_capabilities", "candidate" * 1000, "search")]
        ),
        ModelResponse(
            parts=[
                ToolCallPart("load_capability", {"id": "integral-scaffold"}, "skill")
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart("load_capability", "skill instructions" * 1000, "skill")
            ]
        ),
        ModelResponse(
            parts=[ToolCallPart("coverage", {"blueprint": "b" * 4000}, "coverage")]
        ),
        ModelRequest(parts=[ToolReturnPart("coverage", "covered", "coverage")]),
    ]

    # Seven more pairs put the old search and skill-load outside the recent
    # eight. The loaded skill is still retained by the supported exclusion.
    for index in range(7):
        call_id = f"recent-{index}"
        messages.extend(
            [
                ModelResponse(parts=[ToolCallPart("read", {"index": index}, call_id)]),
                ModelRequest(parts=[ToolReturnPart("read", {"value": index}, call_id)]),
            ]
        )

    compacted = await compaction.compact(messages, SimpleNamespace())

    assert compacted[0].parts[0].args == "{}"
    assert compacted[1].parts[0].content == "[tool result cleared]"
    assert compacted[2].parts[0].args == {"id": "integral-scaffold"}
    assert compacted[3].parts[0].content == "skill instructions" * 1000
    assert compacted[4].parts[0].args == {"blueprint": "b" * 4000}
    assert compacted[5].parts[0].content == "covered"
    assert messages[0].parts[0].args == {"query": "q" * 4000}


@pytest.mark.asyncio
async def test_receipt_filing_compaction_retains_source_destination_and_schema():
    """The normal filing sequence must retain facts needed to map a record."""
    messages = []
    returns = {
        "integral_get_attachment_text": {"text": "Receipt TEST-DEL-002"},
        "integral_rank_destinations": {"track_id": "n.Track.deliveries"},
        "integral_get_track_schema": {"fields": ["reference", "recipient"]},
        "integral_query_entries": {"entries": []},
        "integral_begin_batch": {"batch_token": "pending"},
    }
    for name, content in returns.items():
        messages.extend(
            [
                ModelResponse(parts=[ToolCallPart(name, {}, name)]),
                ModelRequest(parts=[ToolReturnPart(name, content, name)]),
            ]
        )

    compacted = await build_integral_context_compaction().compact(
        messages, SimpleNamespace()
    )

    assert compacted == messages
    assert [
        m.parts[0].content for m in compacted if isinstance(m, ModelRequest)
    ] == list(returns.values())


def test_harness_errors_translate_to_integral_error_codes() -> None:
    """Keep third-party exception semantics inside the binding adapter."""
    assert (
        classify_integral_harness_exception(
            UsageLimitExceeded("Exceeded the total_tokens_limit of 80000")
        )
        == "harness_usage_limit"
    )

    class ModelContextFailure(Exception):
        message = "Model token limit exceeded"

    assert (
        classify_integral_harness_exception(ModelContextFailure())
        == "model_context_limit"
    )
    assert classify_integral_harness_exception(RuntimeError("unexpected")) is None
