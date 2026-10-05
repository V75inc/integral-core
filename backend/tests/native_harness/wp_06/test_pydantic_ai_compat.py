"""Contract tests for Integral's version-sensitive Pydantic AI adapter."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)

from app.agentive.harness.pydantic_ai_compat import (
    RunContext,
    UsageLimitExceeded,
    build_integral_context_compaction,
    build_integral_json_schema_tool,
    build_integral_run_instructions,
    classify_integral_harness_exception,
)


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


def test_integral_compaction_preserves_only_the_current_tool_pair() -> None:
    """Keep the live outcome and skill state while compacting bulky history."""
    compaction = build_integral_context_compaction()

    assert compaction.max_tokens == 12_000
    assert compaction.keep_pairs == 1
    assert compaction.exclude_tools == frozenset({"load_capability"})
    assert compaction.clear_tool_inputs is True


@pytest.mark.asyncio
async def test_compaction_keeps_skill_load_and_latest_result_and_clears_old_payloads() -> (
    None
):
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

    compacted = await compaction.compact(messages, SimpleNamespace())

    assert compacted[0].parts[0].args == "{}"
    assert compacted[1].parts[0].content == "[tool result cleared]"
    assert compacted[2].parts[0].args == {"id": "integral-scaffold"}
    assert compacted[3].parts[0].content == "skill instructions" * 1000
    assert compacted[4].parts[0].args == {"blueprint": "b" * 4000}
    assert compacted[5].parts[0].content == "covered"
    assert messages[0].parts[0].args == {"query": "q" * 4000}


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
