"""Generic context regressions found by replaying a populated Business chat."""

import json
from types import SimpleNamespace

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import (
    InstructionPart,
    LoadCapabilityCallPart,
    LoadCapabilityReturnPart,
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
from pydantic_ai_harness.compaction import SummarizingCompaction, TieredCompaction

from app.agentive.harness.pydantic_ai_compat import (
    IntegralHistorySummary,
    IntegralToolResultCompaction,
    IntegralWorkingContext,
)
from app.agentive.harness.pydantic_context import (
    IntegralContextAdmission,
    IntegralContextPolicy,
)


@pytest.mark.asyncio
async def test_compaction_uses_admission_wire_estimate_with_optimistic_usage(
    monkeypatch,
):
    """Large instructions must trigger recovery before the final admission guard."""
    import app.agentive.harness.pydantic_context as context_module

    monkeypatch.setattr(context_module, "estimate_context_tokens", lambda *a, **k: 10)
    calls = []

    class CheapPass:
        async def compact(self, messages, ctx):
            return messages

    async def compact(self, ctx, request):
        calls.append(True)
        return request

    monkeypatch.setattr(TieredCompaction, "before_model_request", compact)
    request = SimpleNamespace(
        messages=[ModelRequest(parts=[UserPromptPart("A brief request")])],
        model=SimpleNamespace(context_window=32_768),
        model_settings={},
        model_request_parameters=SimpleNamespace(
            instruction_parts=[
                InstructionPart(content="large schema catalogue " * 1000)
            ],
            function_tools=[],
        ),
    )
    pipeline = IntegralWorkingContext(
        tiers=[CheapPass()],
        target_tokens=250,
        policy=IntegralContextPolicy(trigger_tokens=500, target_tokens=250),
    )
    await pipeline.before_model_request(SimpleNamespace(messages=[]), request)
    assert calls, "Admission's wire-size floor must also trigger compaction"


@pytest.mark.asyncio
async def test_summary_does_not_duplicate_active_capability_load(monkeypatch):
    """Historical summarization cannot replay an already retained activation pair."""
    history = [ModelRequest(parts=[UserPromptPart("Earlier task")])]
    history += [ModelResponse(parts=[TextPart("Completed")]) for _ in range(3)]
    current = [
        ModelRequest(parts=[UserPromptPart("New task")]),
        ModelResponse(
            parts=[LoadCapabilityCallPart("load-1", args={"id": "workflow"})]
        ),
        ModelRequest(
            parts=[
                LoadCapabilityReturnPart(
                    "load-1",
                    content={"id": "workflow", "instructions": "Read carefully"},
                )
            ]
        ),
    ]

    async def summarize(self, messages, ctx):
        return [ModelRequest(parts=[SystemPromptPart("Earlier task completed")])]

    monkeypatch.setattr(SummarizingCompaction, "compact", summarize)
    result = await IntegralHistorySummary(max_tokens=1, keep_messages=1).compact(
        history + current,
        SimpleNamespace(active_capability_ids={"workflow"}, tools={}),
    )
    for part_type in (LoadCapabilityCallPart, LoadCapabilityReturnPart):
        assert (
            sum(
                isinstance(part, part_type) and part.tool_call_id == "load-1"
                for message in result
                for part in message.parts
            )
            == 1
        )
    assert result[-len(current) :] == current


@pytest.mark.asyncio
async def test_reclaimed_reads_do_not_fail_admission_on_the_old_provider_usage():
    """A native clearing pass must reduce Core's guard as well as its own estimate."""
    usage = RequestUsage(input_tokens=20_000, output_tokens=20)
    original = "completed read " * 5000
    history = [
        ModelRequest(parts=[UserPromptPart("Earlier task")]),
        ModelResponse(parts=[ToolCallPart("read", {}, "read-1")]),
        ModelRequest(parts=[ToolReturnPart("read", original, "read-1")]),
        ModelResponse(parts=[TextPart("Earlier task complete")], usage=usage),
    ]
    calls = []

    def respond(messages, info):
        calls.append(True)
        assert original not in str(messages)
        return ModelResponse(parts=[TextPart("Current task complete")])

    policy = IntegralContextPolicy(input_tokens=4096)
    agent = Agent(
        FunctionModel(respond),
        capabilities=[
            IntegralWorkingContext(
                tiers=[
                    IntegralToolResultCompaction(
                        max_tokens=1,
                        keep_pairs=0,
                        clearable_tools=frozenset({"read"}),
                    )
                ],
                target_tokens=4096,
                policy=policy,
            ),
            IntegralContextAdmission(policy=policy),
        ],
    )
    result = await agent.run("A brief new question", message_history=history)
    assert result.output == "Current task complete"
    assert calls == [True]
    assert usage.input_tokens == 20_000
    assert history[2].parts[0].content == original


@pytest.mark.asyncio
async def test_mixed_wrapper_reads_compact_per_call_and_preserve_writes():
    from pydantic_ai.messages import ModelMessagesTypeAdapter

    name = "integral_call_workspace_tool"
    payload = "read row " * 4000
    messages = [ModelRequest(parts=[UserPromptPart("Current task")])]
    for index in range(8):
        messages += [
            ModelResponse(parts=[ToolCallPart(name, {"tool_key": "read"}, str(index))]),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        name,
                        payload,
                        str(index),
                        metadata={"integral_effect_v1": "read"},
                    )
                ]
            ),
        ]
    write_payload = {"integral_effect_v1": "read", "receipt": "exact effect"}
    messages += [
        ModelResponse(parts=[ToolCallPart(name, {"tool_key": "write"}, "write")]),
        ModelRequest(parts=[ToolReturnPart(name, write_payload, "write")]),
    ]
    # Trusted framework metadata survives a real checkpoint round trip.
    restored = ModelMessagesTypeAdapter.validate_json(
        ModelMessagesTypeAdapter.dump_json(messages)
    )
    compactor = IntegralToolResultCompaction(
        max_tokens=8192, clearable_tools=frozenset()
    )
    result = await compactor.compact(restored, SimpleNamespace())
    returns = [p for m in result for p in m.parts if type(p) is ToolReturnPart]
    assert [p.content for p in returns[:6]] == [compactor.placeholder] * 6
    assert all("Read preview truncated" in p.content for p in returns[6:8])
    assert returns[-1].content == write_payload
    assert [p.tool_call_id for p in returns] == [str(i) for i in range(8)] + ["write"]
    assert messages[2].parts[0].content == payload


def test_current_skill_bodies_consume_the_historical_disclosure_budget():
    from app.agentive.harness.pydantic_context import project_disclosures

    messages = []
    for name in ("completed", "current"):
        messages += [
            ModelRequest(parts=[UserPromptPart(name)]),
            ModelResponse(parts=[LoadCapabilityCallPart(name, args={"id": name})]),
            ModelRequest(
                parts=[
                    LoadCapabilityReturnPart(
                        name, content={"id": name, "instructions": "procedure " * 3000}
                    )
                ]
            ),
        ]
    projected = project_disclosures(messages, IntegralContextPolicy())
    loads = [
        p for m in projected for p in m.parts if isinstance(p, LoadCapabilityReturnPart)
    ]
    assert [p.tool_call_id for p in loads] == ["current"]
    assert projected[-3:] == messages[-3:]
    assert len(messages) == 6


@pytest.mark.asyncio
async def test_static_and_dynamic_reads_share_one_active_result_window():
    wrapper = "integral_call_workspace_tool"
    messages = [ModelRequest(parts=[UserPromptPart("Current task")])]
    for index, name in enumerate((wrapper, wrapper, "read", "read")):
        messages += [
            ModelResponse(parts=[ToolCallPart(name, {}, str(index))]),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        name,
                        "record " * 2000,
                        str(index),
                        metadata=(
                            {"integral_effect_v1": "read"} if name == wrapper else None
                        ),
                    )
                ]
            ),
        ]
    compactor = IntegralToolResultCompaction(
        max_tokens=8192, clearable_tools=frozenset({"read"})
    )
    result = await compactor.compact(messages, SimpleNamespace())
    returns = [p for m in result for p in m.parts if type(p) is ToolReturnPart]
    assert [p.content for p in returns[:2]] == [compactor.placeholder] * 2
    assert all(p.content.startswith("record ") for p in returns[2:])


@pytest.mark.asyncio
async def test_small_app_reads_keep_only_a_bounded_recent_evidence_window():
    """Keep recent complete reads without accumulating unbounded receipts."""
    wrapper = "integral_call_workspace_tool"
    messages = [ModelRequest(parts=[UserPromptPart("Compare the records")])]
    for index in range(6):
        messages.extend(
            [
                ModelResponse(parts=[ToolCallPart(wrapper, {}, str(index))]),
                ModelRequest(
                    parts=[
                        ToolReturnPart(
                            wrapper,
                            {"output": {"value": str(index), "data": "x" * 1500}},
                            str(index),
                            metadata={"integral_effect_v1": "read"},
                        )
                    ]
                ),
            ]
        )
    for index in range(3):
        messages.extend(
            [
                ModelResponse(parts=[ToolCallPart("read", {}, f"r{index}")]),
                ModelRequest(
                    parts=[ToolReturnPart("read", "data " * 500, f"r{index}")]
                ),
            ]
        )
    compactor = IntegralToolResultCompaction(
        max_tokens=8192, clearable_tools=frozenset({"read"})
    )
    result = await compactor.compact(messages, SimpleNamespace())
    returns = [
        p
        for m in result
        for p in m.parts
        if type(p) is ToolReturnPart and p.tool_name == wrapper
    ]
    assert [p.content for p in returns[:2]] == [compactor.placeholder] * 2
    assert [p.content["output"]["value"] for p in returns[2:]] == ["2", "3", "4", "5"]


@pytest.mark.asyncio
async def test_app_evidence_limit_counts_serialized_unicode_bytes():
    """Escaped multibyte content cannot bypass the retained read budget."""
    wrapper = "integral_call_workspace_tool"
    messages = [
        ModelRequest(parts=[UserPromptPart("Compare records")]),
        ModelResponse(parts=[ToolCallPart(wrapper, {}, "app")]),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    wrapper,
                    {"output": "é" * 1500},
                    "app",
                    metadata={"integral_effect_v1": "read"},
                )
            ]
        ),
    ]
    for index in range(3):
        messages.extend(
            [
                ModelResponse(parts=[ToolCallPart("read", {}, str(index))]),
                ModelRequest(parts=[ToolReturnPart("read", "data " * 500, str(index))]),
            ]
        )
    compactor = IntegralToolResultCompaction(
        max_tokens=8192, clearable_tools=frozenset({"read"})
    )
    result = await compactor.compact(messages, SimpleNamespace())
    assert result[2].parts[0].content == compactor.placeholder


@pytest.mark.asyncio
async def test_active_catalogue_keeps_whole_schemas_with_a_bounded_preview():
    """Discovered schemas survive subsequent reads without hiding truncation."""
    name = "integral_list_workspace_tools"
    rows = [
        {"key": f"tool-{index}", "parameters_schema": {"description": "x" * 1200}}
        for index in range(12)
    ]
    messages = [
        ModelRequest(parts=[UserPromptPart("Compare the records")]),
        ModelResponse(parts=[ToolCallPart(name, {}, "catalogue")]),
        ModelRequest(parts=[ToolReturnPart(name, {"tools": rows}, "catalogue")]),
    ]
    for index in range(3):
        messages += [
            ModelResponse(parts=[ToolCallPart("read", {}, str(index))]),
            ModelRequest(parts=[ToolReturnPart("read", "data " * 500, str(index))]),
        ]
    compactor = IntegralToolResultCompaction(
        max_tokens=8192, clearable_tools=frozenset({name, "read"})
    )
    result = await compactor.compact(messages, SimpleNamespace())
    preview = result[2].parts[0].content
    assert preview["preview_incomplete"] is True
    assert 0 < len(preview["tools"]) < len(rows)
    assert preview["tools"] == rows[: len(preview["tools"])]
    assert len(str(preview).encode()) < 8192
    complete_page = compactor._catalogue_preview(
        ToolReturnPart(
            name, {"tools": rows[:1], "has_more": True, "next_offset": 1}, "page"
        )
    ).content
    assert complete_page["preview_incomplete"] is True
    assert complete_page["next_offset"] == 1


@pytest.mark.asyncio
async def test_resource_lookup_survives_reads_with_honest_bounded_preview():
    """Keep recent lookup evidence without cutting schemas or hiding omissions."""
    name = "integral_find_workspace_resources"
    rows = [
        {"id": f"a{n}", "read_tools": [{"description": "é" * 300}]} for n in range(10)
    ]
    messages = [ModelRequest(parts=[UserPromptPart("Compare records")])]
    for n in range(3):
        messages.extend(
            [
                ModelResponse(parts=[ToolCallPart(name, {}, str(n))]),
                ModelRequest(
                    parts=[
                        ToolReturnPart(
                            name,
                            {"results": rows, "mode": "hybrid", "has_more": True},
                            str(n),
                        )
                    ]
                ),
            ]
        )
    for n in range(3):
        messages.extend(
            [
                ModelResponse(parts=[ToolCallPart("read", {}, f"r{n}")]),
                ModelRequest(parts=[ToolReturnPart("read", "data " * 500, f"r{n}")]),
            ]
        )
    compactor = IntegralToolResultCompaction(
        max_tokens=8192, clearable_tools=frozenset({name, "read"})
    )
    result = await compactor.compact(messages, SimpleNamespace())
    lookups = [
        p
        for m in result
        for p in m.parts
        if type(p) is ToolReturnPart and p.tool_name == name
    ]
    assert lookups[0].content == compactor.placeholder
    for part in lookups[1:]:
        assert part.content["preview_incomplete"]
        assert part.content["results"] == rows[: len(part.content["results"])]
        assert len(json.dumps(part.content).encode()) < 8192


@pytest.mark.asyncio
async def test_track_schema_previews_survive_reads_without_unbounded_context():
    """Retain two clearly incomplete schemas and clear older sensitive detail."""
    name = "integral_get_track_schema"
    messages = [ModelRequest(parts=[UserPromptPart("Compare records")])]
    for index in range(3):
        schema = {
            "track": {"id": f"track-{index}", "title": f"Track {index}"},
            "entry_types": [
                {
                    "key": "item",
                    "name": "Item",
                    "form_schema": {
                        "fields": [
                            {
                                "key": "status",
                                "name": "Status",
                                "type": "select",
                                "enum": ["private"],
                            }
                        ]
                    },
                }
            ],
            "collaborators": [{"display_name": "Private"}],
            "extra": "x" * 50000,
        }
        messages.extend(
            [
                ModelResponse(
                    parts=[
                        ToolCallPart(name, {"track_id": f"track-{index}"}, str(index))
                    ]
                ),
                ModelRequest(parts=[ToolReturnPart(name, schema, str(index))]),
            ]
        )
    for index in range(4):
        messages.extend(
            [
                ModelResponse(parts=[ToolCallPart("read", {}, f"r{index}")]),
                ModelRequest(
                    parts=[ToolReturnPart("read", "data " * 500, f"r{index}")]
                ),
            ]
        )
    compactor = IntegralToolResultCompaction(
        max_tokens=8192, clearable_tools=frozenset({name, "read"})
    )
    result = await compactor.compact(messages, SimpleNamespace())
    schemas = [
        part
        for message in result
        for part in message.parts
        if type(part) is ToolReturnPart and part.tool_name == name
    ]
    assert schemas[0].content == compactor.placeholder
    for index, part in enumerate(schemas[1:], start=1):
        assert part.content["track"]["id"] == f"track-{index}"
        assert part.content["entry_types"][0]["fields"] == [
            {"key": "status", "name": "Status", "type": "select"}
        ]
        assert part.content["schema_preview_incomplete"] is True
        assert "collaborators" not in part.content
        assert len(json.dumps(part.content).encode()) <= 6144
        assert compactor._track_schema_preview(part).content == part.content


@pytest.mark.asyncio
async def test_track_schema_preview_expires_after_effect_and_new_turn():
    """Old schema hints cannot survive a possible change or a later turn."""
    name = "integral_get_track_schema"
    schema = {"track": {"id": "t", "title": "Track"}, "entry_types": []}
    messages = [
        ModelRequest(parts=[UserPromptPart("Earlier question")]),
        ModelResponse(parts=[ToolCallPart(name, {"track_id": "t"}, "old")]),
        ModelRequest(parts=[ToolReturnPart(name, schema, "old")]),
        ModelRequest(parts=[UserPromptPart("Current question")]),
        ModelResponse(parts=[ToolCallPart(name, {"track_id": "t"}, "current")]),
        ModelRequest(parts=[ToolReturnPart(name, schema, "current")]),
        ModelResponse(parts=[ToolCallPart("integral_change_schema", {}, "effect")]),
        ModelRequest(
            parts=[
                ToolReturnPart("integral_change_schema", {"status": "staged"}, "effect")
            ]
        ),
    ]
    compactor = IntegralToolResultCompaction(
        max_tokens=8192, clearable_tools=frozenset({name})
    )
    projected = compactor._compact_classified_reads(messages)
    assert projected[2].parts[0].content == compactor.placeholder
    assert projected[5].parts[0].content == compactor.placeholder
    assert projected[7].parts[0].content == {"status": "staged"}


@pytest.mark.asyncio
async def test_active_resolved_entry_labels_survive_bounded_compaction():
    """Related labels stay available without retaining private record fields."""
    messages = [ModelRequest(parts=[UserPromptPart("Compare the related records")])]
    for index in range(20):
        name = "integral_resolve_entry"
        messages += [
            ModelResponse(
                parts=[ToolCallPart(name, {"entry_id": str(index)}, str(index))]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        name,
                        {
                            "entry": {
                                "id": f"n.Entry.{index}",
                                "title": f"Label {index}",
                                "track_id": "n.Track.test",
                                "body": "sensitive " * 200,
                            }
                        },
                        str(index),
                    )
                ]
            ),
        ]
    compactor = IntegralToolResultCompaction(
        max_tokens=8192, clearable_tools=frozenset({"integral_resolve_entry"})
    )
    result = await compactor.compact(messages, SimpleNamespace())
    returns = [p for m in result for p in m.parts if type(p) is ToolReturnPart]
    assert all(p.content == compactor.placeholder for p in returns[:4])
    for index, part in enumerate(returns[4:18], start=4):
        assert part.content["identity_preview"] is True
        assert part.content["entry"]["title"] == f"Label {index}"
        assert "body" not in part.content["entry"]
    assert all("body" in p.content["entry"] for p in returns[18:])


@pytest.mark.asyncio
async def test_batch_labels_survive_later_reads_with_bounded_honest_previews():
    """Keep authorized labels while marking omitted results explicitly."""
    name = "integral_resolve_entry_labels"
    messages = [ModelRequest(parts=[UserPromptPart("Compare related records")])]
    for index in range(5):
        rows = [
            {
                "id": f"n.Entry.{index}-{i}",
                "title": "label " + "é" * 200,
                "track_id": "n.Track.test",
                "url": "/entry",
                "body": "private",
            }
            for i in range(50)
        ]
        messages.extend(
            [
                ModelResponse(parts=[ToolCallPart(name, {}, str(index))]),
                ModelRequest(
                    parts=[
                        ToolReturnPart(
                            name, {"entries": rows, "unresolved_count": 2}, str(index)
                        )
                    ]
                ),
            ]
        )
    for index in range(3):
        messages.extend(
            [
                ModelResponse(parts=[ToolCallPart("read", {}, f"r{index}")]),
                ModelRequest(
                    parts=[ToolReturnPart("read", "data " * 500, f"r{index}")]
                ),
            ]
        )
    compactor = IntegralToolResultCompaction(
        max_tokens=8192, clearable_tools=frozenset({name, "read"})
    )
    result = await compactor.compact(messages, SimpleNamespace())
    returns = [
        p
        for m in result
        for p in m.parts
        if type(p) is ToolReturnPart and p.tool_name == name
    ]
    assert returns[0].content == compactor.placeholder
    for part in returns[1:]:
        preview = part.content
        assert preview["identity_preview"] and preview["preview_incomplete"]
        assert preview["unresolved_count"] == 2
        assert 0 < len(preview["entries"]) < 50
        assert all("body" not in row for row in preview["entries"])
        assert len(json.dumps(preview).encode()) < 8192


@pytest.mark.asyncio
async def test_active_navigation_survives_data_reads_as_bounded_addresses():
    messages = [ModelRequest(parts=[UserPromptPart("What is the current total?")])]
    for index in range(3):
        rows = [
            {
                "id": f"n.Track.{index}-{row}",
                "title": f"Exact configured title {index}-{row}",
                "url": f"/tracks/n.Track.{index}-{row}",
                "purpose": "description " * 1000,
            }
            for row in range(100)
        ]
        messages += [
            ModelResponse(parts=[ToolCallPart("integral_list_tracks", {}, str(index))]),
            ModelRequest(
                parts=[
                    ToolReturnPart("integral_list_tracks", {"tracks": rows}, str(index))
                ]
            ),
        ]
    for index in range(4):
        messages += [
            ModelResponse(parts=[ToolCallPart("read", {}, f"data-{index}")]),
            ModelRequest(
                parts=[ToolReturnPart("read", "record " * 2000, f"data-{index}")]
            ),
        ]
    compactor = IntegralToolResultCompaction(
        max_tokens=8192, clearable_tools=frozenset({"read", "integral_list_tracks"})
    )
    result = await compactor.compact(messages, SimpleNamespace())
    returns = [p for m in result for p in m.parts if type(p) is ToolReturnPart]
    assert returns[0].content == compactor.placeholder
    for index, part in enumerate(returns[1:3], start=1):
        assert part.content["navigation_preview"] is True
        assert part.content["preview_incomplete"] is True
        assert part.content["tracks"][0]["title"] == f"Exact configured title {index}-0"
        assert part.content["tracks"][0]["url"] == f"/tracks/n.Track.{index}-0"
        assert len(str(part.content).encode("utf-8")) < 8192
    assert returns[3].content == compactor.placeholder
    assert len(messages[2].parts[0].content["tracks"][0]["purpose"]) > 200


@pytest.mark.asyncio
async def test_admission_budget_can_reclaim_older_reads_after_native_tiers(monkeypatch):
    async def native(self, ctx, request):
        return request

    monkeypatch.setattr(TieredCompaction, "before_model_request", native)
    messages = [ModelRequest(parts=[UserPromptPart("Current task")])]
    for index in range(3):
        messages += [
            ModelResponse(parts=[ToolCallPart("read", {}, str(index))]),
            ModelRequest(parts=[ToolReturnPart("read", "row " * 3500, str(index))]),
        ]
    receipt = {"_kind": "staged_change", "token": "exact pending approval"}
    messages += [
        ModelResponse(parts=[ToolCallPart("write", {}, "effect")]),
        ModelRequest(parts=[ToolReturnPart("write", receipt, "effect")]),
    ]
    request = SimpleNamespace(
        messages=messages,
        model=SimpleNamespace(context_window=32768),
        model_settings={},
        model_request_parameters=SimpleNamespace(
            instruction_parts=[],
            function_tools=[],
            revealed_tool_names=set(),
        ),
    )
    policy = IntegralContextPolicy(input_tokens=6000)
    pipeline = IntegralWorkingContext(
        tiers=[
            IntegralToolResultCompaction(
                max_tokens=8192, clearable_tools=frozenset({"read"})
            )
        ],
        target_tokens=4000,
        policy=policy,
    )
    result = await pipeline.before_model_request(
        SimpleNamespace(messages=messages[:]), request
    )
    from app.agentive.harness.pydantic_context import request_estimate

    assert request_estimate(result) < 6000
    assert result.messages[-1].parts[0].content == receipt
    assert result.messages[0].parts[0].content == "Current task"
    assert result.messages[-3].parts[0].content == "row " * 3500
