"""Generic provider-free regressions for terminating unproductive tool loops."""

from __future__ import annotations

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import UsageLimits

from app.agentive.harness.broker_tools import build_brokered_tools
from app.agentive.harness.contracts import HarnessExecutionScope
from app.agentive.harness.pydantic_ai_compat import IntegralToolDisclosure
from app.schemas.capability_broker import CapabilityResult, ReceiptRef

READ = "integral_lookup_items"
WRITE = "integral_write_item"


def _scope(run_id="run-1"):
    return HarnessExecutionScope(
        tenant_id="workspace-1",
        principal_id="user-1",
        workspace_id="workspace-1",
        thread_id="thread-1",
        session_id="session-1",
        run_id=run_id,
        permission_revision="permission-1",
        capability_version="tools-1",
    )


def _tools(monkeypatch, progress, invoke, *, run_id="run-1"):
    monkeypatch.setattr(
        "app.agentive.services.capability_broker.invoke_declared_capability", invoke
    )
    monkeypatch.setattr(
        "app.agentive.services.capability_broker.infer_source_and_op_class",
        lambda name: ("core", "read" if name == READ else "execute"),
    )
    return build_brokered_tools(
        scope=_scope(run_id),
        run_state={"turn_progress": progress},
        always_available_tools={READ, WRITE},
        catalogue=[
            {
                "name": name,
                "description": "A generic test instrument.",
                "input_schema": {
                    "type": "object",
                    "properties": {"cursor": {"type": "string"}},
                },
            }
            for name in (READ, WRITE)
        ],
    )


def _receipt(index):
    """Distinct receipt identity must not turn identical evidence into progress."""
    return ReceiptRef(
        step_id=f"step-{index}",
        run_id="run-1",
        step_key=f"step-{index}",
        idempotency_key=f"call-{index}",
        status="succeeded",
        capability_key=READ,
        origin="chat",
    )


def test_evidence_checkpoint_is_advisory_and_resets_after_an_effect():
    """Long useful reads remain possible; an effect starts a fresh review window."""
    from app.agentive.harness.turn_progress import TurnProgress

    progress = TurnProgress()
    for index in range(8):
        progress.observe(READ, {"cursor": index}, {"items": [index]}, is_read=True)
        assert progress.evidence_review_due is (index >= 4)
        assert not progress.should_finalize
    progress.observe(WRITE, {}, {"accepted": True}, is_read=False)
    assert not progress.evidence_review_due
    assert not progress.should_finalize


def test_evidence_checkpoint_instructions_preserve_further_authorized_work():
    """The model receives a review cue without an artificial hard stop."""
    from types import SimpleNamespace

    from app.agentive.harness.pydantic_ai_compat import build_integral_run_instructions
    from app.agentive.harness.turn_progress import TurnProgress

    progress = TurnProgress()
    instructions = build_integral_run_instructions("Base instructions", progress)
    ctx = SimpleNamespace(
        active_capability_ids=set(),
        available_tool_names=set(),
        messages=[],
        usage_limits=UsageLimits(request_limit=20, tool_calls_limit=32),
        usage=SimpleNamespace(requests=1, tool_calls=0, total_tokens=0),
    )
    assert "Integral evidence checkpoint" not in instructions(ctx)
    for index in range(5):
        progress.observe(READ, {"cursor": index}, {"items": [index]}, is_read=True)
    text = instructions(ctx)
    assert "Integral evidence checkpoint" in text
    assert "not a tool limit" in text
    assert "incomplete page" in text
    assert not progress.should_finalize


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failed", [False, True], ids=["identical-read", "identical-refusal"]
)
async def test_repeated_read_outcomes_finalize_before_the_generation_limit(
    monkeypatch, failed
):
    """Three identical outcomes reserve a tool-free response before the limit."""
    from app.agentive.harness.turn_progress import TurnProgress

    progress = TurnProgress()
    invocations = []

    async def invoke(**kwargs):
        invocations.append(kwargs)
        return CapabilityResult(
            ok=not failed,
            data=None if failed else {"items": []},
            error_code="source.unavailable" if failed else "",
            message="No permitted source is available." if failed else "",
            receipt=_receipt(len(invocations)),
        )

    tools = _tools(monkeypatch, progress, invoke)
    requests = 0

    def respond(_messages, info):
        nonlocal requests
        requests += 1
        if requests == 4:
            assert (info.model_settings or {}).get("tool_choice") == "none"
            assert progress.should_finalize
            return ModelResponse(
                parts=[TextPart("The observed evidence cannot resolve this request.")]
            )
        assert requests < 4
        return ModelResponse(
            parts=[ToolCallPart(READ, {}, tool_call_id=f"read-{requests}")]
        )

    result = await Agent(
        FunctionModel(respond),
        tools=tools,
        capabilities=[
            IntegralToolDisclosure(require_initial_search=False, turn_progress=progress)
        ],
    ).run("Find the requested item.", usage_limits=UsageLimits(request_limit=10))
    assert result.output == "The observed evidence cannot resolve this request."
    assert len(invocations) == 3
    assert len({call["idempotency_key"] for call in invocations}) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["cursor", "result"])
async def test_legitimate_changed_reads_are_not_a_no_progress_loop(monkeypatch, change):
    """Changed cursors or evidence permit bounded useful reads to continue."""
    from app.agentive.harness.turn_progress import TurnProgress

    progress = TurnProgress()
    invocations = []

    async def invoke(**kwargs):
        invocations.append(kwargs)
        index = len(invocations)
        return CapabilityResult(
            ok=True,
            data={"items": [{"id": f"item-{index}" if change == "result" else "item"}]},
            receipt=_receipt(index),
        )

    tools = _tools(monkeypatch, progress, invoke)
    requests = 0

    def respond(_messages, info):
        nonlocal requests
        requests += 1
        assert not progress.should_finalize
        assert (info.model_settings or {}).get("tool_choice") != "none"
        if requests == 7:
            return ModelResponse(parts=[TextPart("The bounded lookup is complete.")])
        arguments = {"cursor": f"page-{requests}"} if change == "cursor" else {}
        return ModelResponse(
            parts=[ToolCallPart(READ, arguments, tool_call_id=f"read-{requests}")]
        )

    result = await Agent(
        FunctionModel(respond),
        tools=tools,
        capabilities=[
            IntegralToolDisclosure(require_initial_search=False, turn_progress=progress)
        ],
    ).run("Read the available pages.", usage_limits=UsageLimits(request_limit=12))
    assert result.output == "The bounded lookup is complete."
    assert len(invocations) == 6


@pytest.mark.asyncio
async def test_writes_are_never_cached_or_treated_as_identical_read_progress(
    monkeypatch,
):
    """Every write reaches current authorization and dispatch independently."""
    from app.agentive.harness.turn_progress import TurnProgress

    progress = TurnProgress()
    invocations = []

    async def invoke(**kwargs):
        invocations.append(kwargs)
        return CapabilityResult(ok=True, data={"accepted": True})

    tools = _tools(monkeypatch, progress, invoke)
    write = next(tool for tool in tools if tool.name == WRITE)
    from types import SimpleNamespace

    for index in range(5):
        result = await write.function_schema.call(
            {},
            SimpleNamespace(tool_call_id=f"write-{index}", active_capability_ids=set()),
        )
        assert result["accepted"] is True
        assert not progress.should_finalize
    assert len(invocations) == 5
    assert len({call["idempotency_key"] for call in invocations}) == 5


@pytest.mark.asyncio
async def test_progress_observations_do_not_reuse_results_or_authority_between_runs(
    monkeypatch,
):
    """A new run observes fresh outcomes and retains its own call identities."""
    from types import SimpleNamespace

    from app.agentive.harness.turn_progress import TurnProgress

    invocations = []

    async def invoke(**kwargs):
        invocations.append(kwargs)
        return CapabilityResult(ok=True, data={"items": []})

    for run_id in ("run-a", "run-b"):
        progress = TurnProgress()
        tools = _tools(monkeypatch, progress, invoke, run_id=run_id)
        read = next(tool for tool in tools if tool.name == READ)
        for index in range(3):
            await read.function_schema.call(
                {},
                SimpleNamespace(
                    tool_call_id=f"read-{index}", active_capability_ids=set()
                ),
            )
            assert progress.should_finalize is (index == 2)
    assert [call["run_id"] for call in invocations] == ["run-a"] * 3 + ["run-b"] * 3
    assert len({call["idempotency_key"] for call in invocations}) == 6


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "oversized", [False, True], ids=["tool-reserve", "oversized-batch"]
)
async def test_provider_tool_batches_cannot_overrun_allowance_or_finalization(
    monkeypatch, oversized
):
    """Over-budget or provider-ignored finalization calls have no extra effects."""
    from app.agentive.harness.pydantic_ai_compat import FINAL_RESPONSE_FALLBACK
    from app.agentive.harness.turn_progress import TurnProgress

    invocations = []

    async def invoke(**kwargs):
        invocations.append(kwargs)
        return CapabilityResult(ok=True, data={"accepted": True})

    progress = TurnProgress()
    tools = _tools(monkeypatch, progress, invoke)
    requests = 0

    def respond(_messages, info):
        nonlocal requests
        requests += 1
        if oversized:
            if requests == 2:
                assert (info.model_settings or {}).get("tool_choice") == "none"
                assert progress.should_finalize
                assert not invocations
                return ModelResponse(
                    parts=[
                        TextPart(
                            "No actions were executed; the proposed batch exceeded this turn allowance."
                        )
                    ]
                )
            assert requests == 1
            batch = 33
        elif requests == 8:
            # The host reserves a final response after 28 calls, well before
            # the 20-request allowance. Simulate a provider ignoring 'none'.
            assert (info.model_settings or {}).get("tool_choice") == "none"
            batch = 1
        else:
            assert requests < 8
            batch = 4
        return ModelResponse(
            parts=[
                ToolCallPart(WRITE, {}, tool_call_id=f"write-{requests}-{index}")
                for index in range(batch)
            ]
        )

    result = await Agent(
        FunctionModel(respond),
        tools=tools,
        capabilities=[
            IntegralToolDisclosure(require_initial_search=False, turn_progress=progress)
        ],
    ).run(
        "Perform the authorized operations.",
        usage_limits=UsageLimits(request_limit=20, tool_calls_limit=32),
    )
    assert result.output == (
        "No actions were executed; the proposed batch exceeded this turn allowance."
        if oversized
        else FINAL_RESPONSE_FALLBACK
    )
    assert len(invocations) == (0 if oversized else 28)
    assert requests == (2 if oversized else 8)


@pytest.mark.parametrize(
    "last_input, used, expected",
    [(1000, 1000, False), (6000, 1000, True), (100, 9950, False), (100, 9952, True)],
)
def test_token_reserve_uses_latest_prompt_cost_and_minimum_completion_room(
    last_input, used, expected
):
    """Reserve completion room using the most recent prompt and safe floor."""
    from types import SimpleNamespace

    from pydantic_ai.usage import RequestUsage

    from app.agentive.harness.pydantic_ai_compat import final_response_reserved

    ctx = SimpleNamespace(
        usage_limits=UsageLimits(
            request_limit=20, tool_calls_limit=32, total_tokens_limit=12000
        ),
        usage=SimpleNamespace(requests=2, tool_calls=1, total_tokens=used),
        messages=[
            ModelResponse(
                parts=[TextPart("Earlier response")],
                usage=RequestUsage(input_tokens=20000),
            ),
            ModelResponse(
                parts=[TextPart("Latest response")],
                usage=RequestUsage(input_tokens=last_input),
            ),
        ],
    )
    assert final_response_reserved(ctx) is expected


@pytest.mark.parametrize("gains_tools", [False, True], ids=["stalled", "productive"])
def test_discovery_stops_only_after_four_rounds_without_new_capabilities(gains_tools):
    """Discovery advances only with newly available capabilities."""
    from app.agentive.harness.turn_progress import TurnProgress

    progress = TurnProgress()
    available = {"search_capabilities"}
    progress.observe_discovery("initial", frozenset(available))
    for index in range(4):
        if gains_tools:
            available.add(f"integral_read_{index}")
        progress.observe_discovery(f"round-{index}", frozenset(available))
        assert progress.should_finalize is (not gains_tools and index == 3)
        # Re-observing restored tool messages is not another discovery round.
        progress.observe_discovery(f"round-{index}", frozenset(available))
        assert progress.should_finalize is (not gains_tools and index == 3)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stalled", [False, True], ids=["reserved-response", "stalled-progress"]
)
async def test_final_validator_never_claims_an_unsaved_proposal_succeeded(stalled):
    """Finalizing an unsaved proposal gives a factual response without retry."""
    from types import SimpleNamespace

    from app.agentive.harness.turn_progress import TurnProgress
    from app.services.chat_providers.pydantic_ai_provider import (
        _scaffold_completion_validator,
    )

    progress = TurnProgress(reason="No progress." if stalled else "")
    ctx = SimpleNamespace(
        usage_limits=UsageLimits(request_limit=20),
        usage=SimpleNamespace(requests=2 if stalled else 19),
    )
    validator = _scaffold_completion_validator(
        {
            "proposal_attempted": True,
            "proposal_succeeded": False,
            "turn_progress": progress,
        }
    )
    claimed = "Success: the setup is saved, built, verified and ready."
    output = await validator(ctx, claimed)
    assert output != claimed
    assert "couldn't save" in output
    assert "hasn't been built" in output
    assert "this chat" in output


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stalled", [False, True], ids=["reserved-response", "stalled-progress"]
)
async def test_final_validator_reports_applied_but_unverified_work_without_retry(
    stalled,
):
    """The final response preserves applied work while qualifying its status."""
    from types import SimpleNamespace

    from app.agentive.harness.turn_progress import TurnProgress
    from app.services.chat_providers.pydantic_ai_provider import (
        _scaffold_completion_validator,
    )

    progress = TurnProgress(reason="No progress." if stalled else "")
    ctx = SimpleNamespace(
        usage_limits=UsageLimits(request_limit=20),
        usage=SimpleNamespace(requests=2 if stalled else 19),
    )
    validator = _scaffold_completion_validator(
        {
            "build_succeeded": True,
            "verification_succeeded": False,
            "turn_progress": progress,
        }
    )
    claimed = "Success: the setup is verified and ready."
    output = await validator(ctx, claimed)
    assert output != claimed
    assert "was saved" in output
    assert "couldn't check" in output
    assert "before making the same changes again" in output
    assert "this chat" in output


@pytest.mark.asyncio
@pytest.mark.parametrize("request_limit", [5, 10])
async def test_upstream_limit_warning_uses_live_allowance_without_rewriting_user_turns(
    request_limit,
):
    """Warnings are dynamic instructions; marker-bearing human text stays exact."""
    from pydantic_ai.messages import UserPromptPart

    from app.agentive.harness.pydantic_ai_compat import IntegralLimitWarnings

    prompt = "[WarnNearLimits] keep this real constraint"
    requests = 0
    warning_requests = []

    async def read_item():
        return {"observed": True}

    def respond(messages, info):
        nonlocal requests
        requests += 1
        user_parts = [
            part.content
            for message in messages
            for part in message.parts
            if isinstance(part, UserPromptPart)
        ]
        assert user_parts == [prompt]
        warnings = [
            part
            for part in (info.model_request_parameters.instruction_parts or [])
            if part.name == "integral-limit-warning"
        ]
        if warnings:
            warning_requests.append(requests)
            assert all(part.dynamic for part in warnings)
            assert f"/{request_limit} requests used" in warnings[0].content
        if requests == 4:
            assert bool(warnings) is (request_limit == 5)
            return ModelResponse(parts=[TextPart("The observed read is complete.")])
        return ModelResponse(parts=[ToolCallPart("read_item", {})])

    result = await Agent(
        FunctionModel(respond),
        tools=[read_item],
        capabilities=[IntegralLimitWarnings()],
    ).run(prompt, usage_limits=UsageLimits(request_limit=request_limit))
    assert result.output == "The observed read is complete."
    assert warning_requests == ([4] if request_limit == 5 else [])
    assert [
        part.content
        for message in result.all_messages()
        for part in message.parts
        if isinstance(part, UserPromptPart)
    ] == [prompt]


@pytest.mark.parametrize("boundary", ["human", "host", "absent"])
def test_discovery_observer_counts_only_calls_after_an_active_turn_boundary(boundary):
    """Prior discovery never stalls a new human turn or typed Core continuation."""
    from types import SimpleNamespace

    from pydantic_ai.messages import ModelRequest, ToolReturnPart, UserPromptPart

    from app.agentive.harness.pydantic_ai_compat import build_integral_host_turn
    from app.agentive.harness.turn_progress import TurnProgress

    available = frozenset({"search_capabilities"})
    progress = TurnProgress()
    progress.observe_discovery("available-baseline", available)
    messages = (
        []
        if boundary == "absent"
        else [ModelRequest(parts=[UserPromptPart("An earlier request.")])]
    )
    messages.extend(
        ModelRequest(
            parts=[
                ToolReturnPart(
                    "search_capabilities", {"results": []}, f"old-search-{index}"
                )
            ]
        )
        for index in range(4)
    )
    if boundary == "human":
        messages.append(ModelRequest(parts=[UserPromptPart("A new request.")]))
    elif boundary == "host":
        messages.append(
            build_integral_host_turn("Report the current event.", run_id="run-current")
        )
    disclosure = IntegralToolDisclosure(turn_progress=progress)
    disclosure._observe_discovery(
        SimpleNamespace(
            messages=messages,
            available_tool_names=available,
            active_capability_ids=set(),
        )
    )
    assert not progress.should_finalize
    # Exactly four *new* stale rounds are still required. This proves the old
    # calls were not counted, without asserting private implementation fields.
    for index in range(4):
        progress.observe_discovery(f"new-search-{index}", available)
        assert progress.should_finalize is (index == 3)
