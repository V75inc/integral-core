"""Content-free, offline probes of Integral's native context/token boundaries.

Run from the repository root with:
    PYTHONPATH=backend backend/.venv/bin/python scripts/probe_native_context_budget.py

FunctionModel usage below is explicitly synthetic. It demonstrates enforcement,
not provider pricing, tokenizer accuracy, or production workload performance.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from pydantic_ai import Agent, UsageLimitExceeded, UsageLimits
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage

from app.agentive.harness.pydantic_ai_compat import (
    build_integral_context_compaction,
    build_integral_host_turn,
)


def _history():
    messages = [ModelRequest(parts=[UserPromptPart("Synthetic record review")])]
    for index in range(12):
        call_id = str(index)
        messages.extend(
            [
                ModelResponse(parts=[ToolCallPart("read", {}, call_id)]),
                ModelRequest(parts=[ToolReturnPart("read", "x" * 20_000, call_id)]),
            ]
        )
    return messages


def _result_chars(messages):
    return sum(
        len(part.content)
        for message in messages
        for part in message.parts
        if isinstance(part, ToolReturnPart) and isinstance(part.content, str)
    )


async def _limit_probe(token_limit):
    calls = 0

    async def read() -> dict:
        return {"source_id": "synthetic", "complete": True}

    def respond(messages, info):
        nonlocal calls
        calls += 1
        parts = (
            [TextPart("Synthetic task complete")]
            if calls == 20
            else [ToolCallPart("read", {}, str(calls))]
        )
        return ModelResponse(
            parts=parts,
            usage=RequestUsage(input_tokens=40_000, output_tokens=100),
        )

    agent = Agent(FunctionModel(respond), tools=[read])
    try:
        await agent.run(
            "Synthetic twenty-step task",
            usage_limits=UsageLimits(
                request_limit=20, tool_calls_limit=32, total_tokens_limit=token_limit
            ),
        )
        outcome = "completed"
    except UsageLimitExceeded:
        outcome = "token_limit"
    return {
        "usage_source": "synthetic FunctionModel usage, not provider telemetry",
        "token_limit": token_limit,
        "model_calls": calls,
        "synthetic_total_tokens": calls * 40_100,
        "outcome": outcome,
    }


async def main():
    """Write reproducible size and limit evidence without a model provider."""
    history = _history()
    compaction = build_integral_context_compaction()
    active = await compaction.compact(history, SimpleNamespace())
    unmarked = ModelRequest(parts=[SystemPromptPart("Synthetic host event")])
    marked = build_integral_host_turn("Synthetic host event", run_id="probe-run")
    before = await compaction.compact([*history, unmarked], SimpleNamespace())
    after = await compaction.compact([*history, marked], SimpleNamespace())
    # Verify the marker survives the same typed codec used by checkpoints.
    restored = ModelMessagesTypeAdapter.validate_json(
        ModelMessagesTypeAdapter.dump_json(after)
    )
    assert restored[-1].metadata == {"integral_host_turn_v1": "probe-run"}
    evidence = {
        "kind": "offline deterministic probe; no provider calls or billing",
        "active_task": {
            "tool_results": 12,
            "source_result_chars": _result_chars(history),
            "retained_result_chars": _result_chars(active),
            "exact_grounding_preserved": active == history,
        },
        "new_host_event": {
            "unmarked_boundary_result_chars": _result_chars(before),
            "marked_boundary_result_chars": _result_chars(after),
            "retained_recent_tool_pairs": compaction.keep_pairs,
            "checkpoint_marker_round_trip": True,
        },
        "cumulative_limit": [
            await _limit_probe(600_000),
            await _limit_probe(1_200_000),
        ],
    }
    output = Path("docs/ops/qualification/token-budget/2026-10-12-probe.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
