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
    ToolReturn,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel
from pydantic_ai_harness import Skills

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


@pytest.mark.asyncio
async def test_skill_loader_is_not_disclosed_until_current_turn_search_succeeds():
    observed_tools = []

    async def search_capabilities(query: str):
        return ToolReturn(
            {"query": query, "results": ["integral-entries"]},
            tools=["integral_query_entries"],
        )

    async def load_capability(id: str):
        return {"id": id, "instructions": "Manage a record in an existing track."}

    async def integral_query_entries_fn(ctx: RunContext[Any], query: str):
        return {"entries": []}

    async def integral_delete_entry_fn(ctx: RunContext[Any], entry_id: str):
        return {"deleted": entry_id}

    integral_query_entries = build_integral_json_schema_tool(
        function=integral_query_entries_fn,
        name="integral_query_entries",
        description="Search existing entries.",
        json_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
        prepare=None,
        defer_loading=True,
    )
    integral_delete_entry = build_integral_json_schema_tool(
        function=integral_delete_entry_fn,
        name="integral_delete_entry",
        description="Delete one entry.",
        json_schema={
            "type": "object",
            "properties": {"entry_id": {"type": "string"}},
            "required": ["entry_id"],
            "additionalProperties": False,
        },
        prepare=None,
        defer_loading=True,
    )

    def respond(messages, info):
        visible = {tool.name for tool in info.function_tools}
        observed_tools.append(visible)
        if len(observed_tools) == 1:
            assert {"search_capabilities", "load_capability"}.issubset(visible)
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "search_capabilities", {"query": "update an existing record"}
                    )
                ]
            )
        if len(observed_tools) == 2:
            assert {"load_capability", "integral_query_entries"}.issubset(visible)
            assert "integral_delete_entry" not in visible
            return ModelResponse(
                parts=[ToolCallPart("load_capability", {"id": "integral-entries"})]
            )
        return ModelResponse(parts=[TextPart("I found the right workflow.")])

    agent = Agent(
        FunctionModel(respond),
        tools=[
            search_capabilities,
            load_capability,
            integral_query_entries,
            integral_delete_entry,
        ],
        capabilities=[IntegralToolDisclosure()],
    )
    result = await agent.run("Change the status of an existing item.")

    assert result.output == "I found the right workflow."
    assert len(observed_tools) == 3
    assert {"search_capabilities", "load_capability"}.issubset(observed_tools[0])
    assert {"load_capability", "integral_query_entries"}.issubset(observed_tools[1])


@pytest.mark.asyncio
async def test_native_skills_loader_remains_available_after_integral_catalog_search(
    tmp_path: Path,
):
    """Use Pydantic AI's real Skills loader after Integral catalog discovery."""
    skill_dir = tmp_path / "integral-workspace"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: integral-workspace\ndescription: Orient in the workspace.\n---\n"
        "Use the workspace tools to answer orientation questions.\n",
        encoding="utf-8",
    )
    observed_tools: list[set[str]] = []

    async def search_capabilities(query: str):
        return ToolReturn(
            {
                "query": query,
                "results": [
                    {
                        "kind": "skill",
                        "load_with": {
                            "tool": "load_capability",
                            "id": "integral-workspace",
                        },
                    }
                ],
            },
            tools=[],
        )

    def respond(messages, info):
        visible = {tool.name for tool in info.function_tools}
        observed_tools.append(visible)
        choice = (info.model_settings or {}).get("tool_choice", "auto")
        if choice == ["search_capabilities"]:
            return ModelResponse(
                parts=[
                    ToolCallPart("search_capabilities", {"query": "workspace tracks"})
                ]
            )
        if choice == ["load_capability"]:
            assert "load_capability" in visible
            return ModelResponse(
                parts=[ToolCallPart("load_capability", {"id": "integral-workspace"})]
            )
        return ModelResponse(parts=[TextPart("The current workspace is ready.")])

    agent = Agent(
        FunctionModel(respond),
        tools=[search_capabilities],
        capabilities=[
            IntegralToolDisclosure(),
            Skills(tmp_path, include={"integral-workspace"}),
        ],
    )
    result = await agent.run("What workspace and tracks do I have?")

    assert result.output == "The current workspace is ready."
    assert len(observed_tools) == 3
    assert all("load_capability" in names for names in observed_tools)
    assert "Unknown tool name" not in str(result.all_messages())


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


@pytest.mark.parametrize("failed", [False, True])
def test_only_a_recorded_successful_search_releases_initial_choice(failed):
    settings = IntegralToolDisclosure().get_model_settings()
    ctx = SimpleNamespace(
        model=SimpleNamespace(profile={}),
        messages=[
            ModelRequest(
                parts=[ToolReturnPart("search_capabilities", {"error": failed}, "call")]
            )
        ],
    )
    assert settings(ctx) == ({"tool_choice": ["search_capabilities"]} if failed else {})


def test_loading_a_skill_without_search_does_not_release_initial_choice():
    settings = IntegralToolDisclosure().get_model_settings()
    ctx = SimpleNamespace(
        model=SimpleNamespace(profile={}),
        messages=[
            ModelRequest(
                parts=[
                    ToolReturnPart("load_capability", {"instructions": "..."}, "call")
                ]
            )
        ],
    )
    assert settings(ctx) == {"tool_choice": ["search_capabilities"]}


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

    base = instructions(SimpleNamespace(active_capability_ids=set()))
    assert base.startswith("Integral resident agent.")
    assert "copy the canonical url returned by Core tools exactly" in base
    assert "replace its dots with slashes" in base
    assert "plain record label instead of a guessed link" in base
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

    assert compaction.max_tokens == 16_384
    assert compaction.max_fraction is None
    assert compaction.keep_pairs == 5
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

    # Three more pairs put the old search and coverage result outside the
    # recent five. The loaded skill is still retained by the supported
    # exclusion because Pydantic AI derives active state from that receipt.
    for index in range(3):
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


def test_known_pending_workflow_does_not_force_rediscovery():
    settings = IntegralToolDisclosure(require_initial_search=False).get_model_settings()
    ctx = SimpleNamespace(model=SimpleNamespace(profile={}), messages=[])
    assert settings(ctx) == {}


def test_pending_reply_is_semantic_decision_without_rediscovery():
    capability = IntegralToolDisclosure(
        require_initial_search=False,
        pending_decision_tool="integral_resolve_pending_write",
    )
    settings = capability.get_model_settings()
    ctx = SimpleNamespace(model=SimpleNamespace(profile={}), messages=[])
    assert settings(ctx) == {"tool_choice": ["integral_resolve_pending_write"]}
    ctx.messages = [
        ModelRequest(
            parts=[
                ToolReturnPart(
                    "integral_resolve_pending_write",
                    {"decision": "no_decision"},
                    "call",
                )
            ]
        )
    ]
    assert settings(ctx) == {}


@pytest.mark.asyncio
async def test_pending_decision_schema_is_visible_before_catalog_search():
    async def integral_resolve_pending_write(item_reference: str, decision: str):
        assert item_reference == "reference"
        assert decision == "no_decision"
        return {"decision": decision, "applied": False}

    def respond(messages, info):
        choice = (info.model_settings or {}).get("tool_choice", "auto")
        if choice == ["integral_resolve_pending_write"]:
            assert "integral_resolve_pending_write" in {
                tool.name for tool in info.function_tools
            }
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "integral_resolve_pending_write",
                        {"item_reference": "reference", "decision": "no_decision"},
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("Nothing changed; here is the answer.")])

    agent = Agent(
        FunctionModel(respond),
        tools=[integral_resolve_pending_write],
        capabilities=[
            IntegralToolDisclosure(
                require_initial_search=False,
                pending_decision_tool="integral_resolve_pending_write",
            )
        ],
    )
    result = await agent.run("What would this change?")
    assert result.output == "Nothing changed; here is the answer."


@pytest.mark.asyncio
async def test_history_compaction_preserves_loaded_skills_and_user_constraints(
    tmp_path,
):
    """Use the real library across a resumed long chat, including summary usage."""
    from pydantic_ai.usage import RequestUsage

    from app.agentive.harness.pydantic_ai_compat import (
        build_integral_history_compaction,
    )

    skill = tmp_path / "record-work"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: record-work\ndescription: Manage records.\n---\n"
        "Always respect the current record schema.\n"
    )
    observations = []

    def respond(messages, info):
        text = str(messages)
        if "You are a context summarization assistant" in text:
            observations.append("summary")
            return ModelResponse(
                parts=[
                    TextPart(
                        "## Intent\nMaintain the bike shop; no sample data.\n"
                        "## Key decisions\nCustomer Ana exists; rejected bikes were not saved."
                    )
                ],
                usage=RequestUsage(input_tokens=100, output_tokens=30),
            )
        if not observations:
            observations.append("load")
            return ModelResponse(
                parts=[ToolCallPart("load_capability", {"id": "record-work"})]
            )
        if observations == ["load"]:
            observations.append("first")
            return ModelResponse(
                parts=[TextPart("Older explanation. " * 12000)],
                usage=RequestUsage(input_tokens=1000, output_tokens=60000),
            )
        observations.append("resumed")
        assert "current record schema" in str(info.instructions) + text
        assert len(text) < 100000
        assert "no sample data" in text
        return ModelResponse(parts=[TextPart("Ana remains the only customer.")])

    agent = Agent(
        FunctionModel(respond),
        capabilities=[
            Skills(tmp_path, include={"record-work"}),
            build_integral_history_compaction(),
        ],
    )
    first = await agent.run("Maintain my bike shop with no sample data.")
    second = await agent.run(
        "Who is in my customer list?", message_history=first.all_messages()
    )
    assert second.output == "Ana remains the only customer."
    assert observations == ["load", "first", "summary", "resumed"]
    assert second.usage.requests == 2
    fresh_agent = Agent(
        FunctionModel(respond),
        capabilities=[
            Skills(tmp_path, include={"record-work"}),
            build_integral_history_compaction(),
        ],
    )
    third = await fresh_agent.run("And now?", message_history=second.all_messages())
    assert third.output == second.output
    assert observations[-1] == "resumed"


@pytest.mark.asyncio
async def test_history_summary_never_distills_active_batch_receipts(monkeypatch):
    from pydantic_ai_harness.compaction import SummarizingCompaction

    from app.agentive.harness.pydantic_ai_compat import IntegralHistorySummary

    older = [
        ModelRequest(parts=[UserPromptPart(f"Earlier request {i}")]) for i in range(30)
    ]
    current = [
        ModelRequest(
            parts=[UserPromptPart("Save the contact and change the job date.")]
        ),
        ModelResponse(
            parts=[
                ToolCallPart(
                    "integral_update_entry",
                    {"entry_id": "contact"},
                    tool_call_id="queued",
                )
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    "integral_update_entry",
                    {"batched": True, "result_ref": "{{step_1.id}}"},
                    tool_call_id="queued",
                )
            ]
        ),
    ]
    seen = []

    async def summarize(self, messages, ctx):
        seen.extend(messages)
        return [ModelRequest(parts=[UserPromptPart("Earlier work summarized.")])]

    monkeypatch.setattr(SummarizingCompaction, "compact", summarize)
    ctx = SimpleNamespace(active_capability_ids=set(), tools={})
    compacted = await IntegralHistorySummary(max_messages=20).compact(
        older + current, ctx
    )
    assert seen == older
    assert compacted[-3:] == current
    # Reconsidering a growing active turn does not buy another summary or
    # remove the already-queued effect receipt.
    assert (
        await IntegralHistorySummary(max_messages=20).compact(compacted, ctx)
        == compacted
    )
    assert seen == older


@pytest.mark.asyncio
async def test_compaction_preserves_active_multi_record_grounding():
    messages = [ModelRequest(parts=[UserPromptPart("Prepare the related records")])]
    for index in range(12):
        messages.extend(
            [
                ModelResponse(
                    parts=[ToolCallPart("read", {"index": index}, str(index))]
                ),
                ModelRequest(
                    parts=[
                        ToolReturnPart("read", {"record_id": str(index)}, str(index))
                    ]
                ),
            ]
        )
    compacted = await build_integral_context_compaction().compact(
        messages, SimpleNamespace()
    )
    assert compacted == messages
    assert (
        len(
            [
                p
                for m in compacted
                for p in m.parts
                if isinstance(p, ToolReturnPart) and isinstance(p.content, dict)
            ]
        )
        == 12
    )


@pytest.mark.asyncio
async def test_compaction_clears_completed_turn_but_preserves_new_task():
    messages = [ModelRequest(parts=[UserPromptPart("Earlier completed work")])]
    for index in range(8):
        messages.extend(
            [
                ModelResponse(
                    parts=[ToolCallPart("read", {"index": index}, str(index))]
                ),
                ModelRequest(
                    parts=[
                        ToolReturnPart("read", {"record_id": str(index)}, str(index))
                    ]
                ),
            ]
        )
    active = [ModelRequest(parts=[UserPromptPart("New task")])]
    for index in range(10, 18):
        active.extend(
            [
                ModelResponse(
                    parts=[ToolCallPart("read", {"index": index}, str(index))]
                ),
                ModelRequest(
                    parts=[
                        ToolReturnPart("read", {"record_id": str(index)}, str(index))
                    ]
                ),
            ]
        )
    compacted = await build_integral_context_compaction().compact(
        messages + active, SimpleNamespace()
    )
    assert compacted[-len(active) :] == active
    assert compacted[2].parts[0].content == "[tool result cleared]"
    assert messages[2].parts[0].content == {"record_id": "0"}
