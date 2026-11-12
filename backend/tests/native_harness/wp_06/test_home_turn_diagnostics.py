"""Safe Home diagnostics and finite tool-free turn handoff."""

from types import SimpleNamespace

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import UsageLimits

from app.agentive.harness.events import translate_event
from app.agentive.harness.pydantic_ai_compat import (
    FunctionToolResultEvent,
    IntegralToolDisclosure,
    build_integral_run_instructions,
)
from app.agentive.services.work_worker import _native_chat_transcript
from app.services.chat_tool_diagnostics import normalize_tool_diagnostic
from app.services.chat_turn_events import normalize_public_event
from app.services.chat_turn_transcript import _public_parts


def test_broker_error_survives_replay_as_content_free_failure():
    event = list(
        translate_event(
            FunctionToolResultEvent(
                part=ToolReturnPart(
                    tool_name="integral_update_app",
                    tool_call_id="update-1",
                    content={
                        "error": True,
                        "error_code": "invalid_arguments",
                        "message": "private value secret-sentinel",
                        "args": {"private": "secret-sentinel"},
                    },
                )
            )
        )
    )[0]
    assert event["status"] == "error"
    replay = normalize_public_event(event)
    assert "secret-sentinel" not in str(replay)
    assert "result" not in replay and "args" not in replay
    parts, _ = _native_chat_transcript([replay])
    assert _public_parts(parts) == parts
    assert parts[0]["isError"] is True
    assert parts[0]["diagnostic"]["code"] == "invalid_arguments"


@pytest.mark.parametrize(
    "diagnostic",
    [
        {"code": "invalid_arguments", "message": "secret-sentinel"},
        {"code": "secret-sentinel", "message": "secret-sentinel"},
        {"code": "tool_failed", "message": "anything", "args": {}},
    ],
)
def test_diagnostic_contract_rejects_arbitrary_content(diagnostic):
    with pytest.raises(ValueError):
        normalize_tool_diagnostic(diagnostic)
    with pytest.raises(ValueError):
        normalize_public_event({"type": "tool-call", "diagnostic": diagnostic})


@pytest.mark.asyncio
@pytest.mark.parametrize("ignore_reserve", [False, True])
async def test_final_request_is_reserved_for_handoff_without_another_effect(
    ignore_reserve,
):
    calls = 0
    effects = []

    def respond(messages, info):
        nonlocal calls
        calls += 1
        if calls < 4:
            return ModelResponse(parts=[ToolCallPart("observe", {})])
        assert "final available response" in messages[-1].instructions
        if ignore_reserve:
            return ModelResponse(parts=[ToolCallPart("observe", {})])
        return ModelResponse(
            parts=[TextPart("The requested work is unfinished; continue in this chat.")]
        )

    def observe():
        effects.append("read")
        return {"state": "observed"}

    agent = Agent(
        FunctionModel(respond),
        tools=[observe],
        capabilities=[IntegralToolDisclosure(require_initial_search=False)],
        instructions=build_integral_run_instructions("Use observed receipts only."),
    )
    result = await agent.run("Do the work", usage_limits=UsageLimits(request_limit=4))
    if ignore_reserve:
        from app.agentive.harness.turn_progress import FINAL_RESPONSE_FALLBACK

        assert result.output == FINAL_RESPONSE_FALLBACK
        assert "pending approval is not a saved change" in result.output
    else:
        assert "unfinished" in result.output
    assert calls == 4
    assert effects == ["read"] * 3


def test_handoff_uses_live_allowance_and_does_not_mutate_it():
    from app.agentive.harness.pydantic_ai_compat import final_response_reserved

    allowance = UsageLimits(request_limit=3)
    ctx = SimpleNamespace(usage_limits=allowance, usage=SimpleNamespace(requests=2))
    assert final_response_reserved(ctx)
    assert allowance.request_limit == 3


@pytest.mark.asyncio
async def test_home_extra_keys_and_custom_validation_never_echo_input():
    from app.services.app_home_override import validate_home_composition

    with pytest.raises(ValueError) as error:
        await validate_home_composition(
            user_id="user-1",
            app_id="app-1",
            home={"secret-sentinel-key": "secret-sentinel-value"},
        )
    assert "secret-sentinel" not in str(error.value)
    assert "title: required field" in str(error.value)
    assert "widgets: required field" in str(error.value)


@pytest.mark.asyncio
async def test_home_source_denial_is_actionable_and_creates_no_approval(monkeypatch):
    from unittest.mock import AsyncMock

    from app.agentive.tooling.dispatch import dispatch_tool

    monkeypatch.setattr(
        "app.agentive.tooling.dispatch.enforce_tool_policy",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.services.app_home_override.validate_dashboard_field_bindings",
        AsyncMock(side_effect=PermissionError("secret-sentinel-source")),
    )
    stage = AsyncMock()
    monkeypatch.setattr("app.agentive.staging.create_staged_change", stage)
    result = await dispatch_tool(
        "integral_update_app",
        {
            "app_id": "app-1",
            "home": {
                "title": "Home",
                "widgets": [{"id": "one", "type": "metric_card"}],
            },
        },
        principal_id="user-1",
        scope="workspace-1",
    )
    assert result.error_code == "permission_denied"
    assert "secret-sentinel" not in result.message
    stage.assert_not_called()


_ALREADY_ACTIVE = (
    "Capability 'integral-scaffold' is already active. "
    "Use its existing instructions and any tools it provides; "
    "do not call `load_capability` for it again."
)


def test_duplicate_capability_load_has_precise_content_free_diagnostic():
    from pydantic_ai.messages import RetryPromptPart

    event = list(
        translate_event(
            FunctionToolResultEvent(
                part=RetryPromptPart(
                    tool_name="load_capability",
                    tool_call_id="load-duplicate",
                    content=_ALREADY_ACTIVE,
                )
            )
        )
    )[0]
    # It remains a retry/failure, not a fabricated completed tool effect.
    assert event["status"] == "error"
    assert event["summary"]["outcome"] == "failed"
    public = normalize_public_event(event)
    assert public["diagnostic"]["code"] == "capability_already_active"
    assert "integral-scaffold" not in str(public)
    parts, _ = _native_chat_transcript([public])
    assert _public_parts(parts) == parts
    assert parts[0]["isError"] is True
    assert parts[0]["diagnostic"]["message"] == (
        "The requested capability is already active; no additional loading was needed."
    )


@pytest.mark.parametrize(
    "tool_name,content,is_retry",
    [
        ("integral_update_app", _ALREADY_ACTIVE, True),
        ("load_capability", _ALREADY_ACTIVE + " secret-sentinel", True),
        ("load_capability", "No capability found with id 'private'.", True),
        ("load_capability", {"error": True, "message": _ALREADY_ACTIVE}, False),
        (
            "load_capability",
            {"error": True, "error_code": "capability_already_active"},
            False,
        ),
    ],
)
def test_other_tool_failures_do_not_masquerade_as_duplicate_load(
    tool_name, content, is_retry
):
    from pydantic_ai.messages import RetryPromptPart

    part_type = RetryPromptPart if is_retry else ToolReturnPart
    event = list(
        translate_event(
            FunctionToolResultEvent(
                part=part_type(
                    tool_name=tool_name, tool_call_id="call-other", content=content
                )
            )
        )
    )[0]
    assert event["status"] == "error"
    assert event["diagnostic"]["code"] == "tool_failed"
    assert "secret-sentinel" not in str(normalize_public_event(event))
