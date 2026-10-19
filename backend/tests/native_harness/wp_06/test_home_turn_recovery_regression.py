"""Provider-free Home authoring regression through the real tool/stager path."""

from __future__ import annotations

import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jsonschema
import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import UsageLimits

from app.agentive.harness.broker_tools import build_brokered_tools
from app.agentive.harness.contracts import HarnessExecutionScope
from app.agentive.services.capability_adapters import AdapterError, dispatch_capability
from app.agentive.tooling.catalogue import build_tool_catalogue
from app.schemas.capability_broker import CapabilityInvocation, CapabilityResult


def _catalogue():
    return [
        item for item in build_tool_catalogue() if item["name"] == "integral_update_app"
    ]


def _home():
    return {
        "title": "Home",
        "widgets": [
            {
                "id": "overview",
                "type": "metric_card",
                "grid": {"x": 0, "y": 0, "w": 4, "h": 2},
                "data_source": {"kind": "count"},
            }
        ],
    }


def test_home_tool_exposes_an_actionable_validated_contract():
    """The authoring schema must distinguish valid Home/reset from guessed shapes."""
    schema = _catalogue()[0]["input_schema"]
    validator = jsonschema.Draft202012Validator(schema)
    validator.check_schema(schema)
    assert not list(validator.iter_errors({"app_id": "app-1", "home": _home()}))
    assert not list(validator.iter_errors({"app_id": "app-1", "home": {}}))
    for malformed in (
        {"widgets": _home()["widgets"]},
        {"title": "Home", "widgets": []},
        {"title": "Home", "widgets": [{"type": "metric_card"}]},
        {"title": "Home", "widgets": _home()["widgets"], "invented": True},
    ):
        assert list(validator.iter_errors({"app_id": "app-1", "home": malformed})), (
            "integral_update_app exposes an opaque Home object instead of the "
            "actual title/widget contract"
        )


@pytest.mark.asyncio
async def test_scripted_home_turn_repairs_validation_and_stages_once(monkeypatch):
    """Malformed → precise safe feedback → corrected proposal in three responses.

    No external model/provider runs. Authority and storage are explicit test
    boundaries; framework argument handling, Core adapter, dispatch and Home
    stager/shape/layout validation are the production implementations.
    """
    from app.services import app_home_override

    for path in (
        "app.services.prompt_queue.session_queue_is_open",
        "app.services.chat_threads.design_awaiting_user_response",
        "app.services.chat_threads.design_amend_required",
        "app.services.chat_threads.design_proposed_pending",
    ):
        monkeypatch.setattr(path, AsyncMock(return_value=False))
    monkeypatch.setattr("app.services.prompt_queue.enqueue_staged_write", AsyncMock())

    monkeypatch.setattr(
        "app.agentive.tooling.dispatch.enforce_tool_policy",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        app_home_override, "validate_dashboard_field_bindings", AsyncMock()
    )
    monkeypatch.setattr(
        "app.agentive.unstaged_targets.is_unstaged_target",
        AsyncMock(return_value=False),
    )
    staged = []

    async def create_staged_change(**kwargs):
        staged.append(kwargs)
        return SimpleNamespace(
            to_dict=lambda: {
                "token": "pending-home-1",
                "kind": "update_app",
                "state": "pending",
            }
        )

    monkeypatch.setattr(
        "app.agentive.staging.create_staged_change", create_staged_change
    )
    monkeypatch.setattr("app.agentive.staging.is_batch_open", lambda *args: False)

    # This bridge isolates run/snapshot persistence, preserving the actual
    # capability adapter and its production error normalization.
    async def invoke_declared(**kwargs):
        assert kwargs["principal_id"] == "user-1"
        assert kwargs["workspace_id"] == "workspace-1"
        inv = CapabilityInvocation(
            **{
                key: kwargs[key]
                for key in CapabilityInvocation.model_fields
                if key in kwargs
            }
        )
        try:
            data = await dispatch_capability(inv, {})
        except AdapterError as exc:
            return CapabilityResult(
                ok=False, error_code=exc.error_code, message=exc.message
            )
        return CapabilityResult(ok=True, data=data)

    monkeypatch.setattr(
        "app.agentive.services.capability_broker.invoke_declared_capability",
        invoke_declared,
    )
    scope = HarnessExecutionScope(
        tenant_id="workspace-1",
        principal_id="user-1",
        workspace_id="workspace-1",
        thread_id="thread-1",
        session_id="session-1",
        run_id="run-1",
        permission_revision="permissions-1",
        capability_version="tools-1",
    )
    # No session-backed design/prompt gates are exercised by this isolated
    # turn; the real Home proposal still receives server-bound principal/scope.
    tools = build_brokered_tools(
        scope=scope,
        catalogue=_catalogue(),
        conversation_id="",
        always_available_tools={"integral_update_app"},
    )
    calls = 0
    feedback = []
    invalid = copy.deepcopy(_home())
    invalid["widgets"][0]["id"] = {"private": "secret-sentinel-do-not-echo"}

    def respond(messages, _info):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "integral_update_app", {"app_id": "app-1", "home": invalid}
                    )
                ]
            )
        latest = [
            part
            for message in messages
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ][-1].content
        feedback.append(latest)
        if calls == 2:
            assert latest["error_code"] == "invalid_arguments"
            assert "widgets.0.id" in latest["message"]
            assert "secret-sentinel-do-not-echo" not in latest["message"]
            assert "input_value" not in latest["message"]
            assert not staged
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "integral_update_app", {"app_id": "app-1", "home": _home()}
                    )
                ]
            )
        assert latest["token"] == "pending-home-1"
        return ModelResponse(
            parts=[TextPart("The Home tab proposal is ready for approval.")]
        )

    result = await Agent(FunctionModel(respond), tools=tools).run(
        "Add a Home tab to this app", usage_limits=UsageLimits(request_limit=4)
    )
    assert result.output == "The Home tab proposal is ready for approval."
    assert calls == 3
    assert len(feedback) == 2
    assert len(staged) == 1
    assert staged[0]["payload"]["home"]["widgets"][0]["id"] == "overview"
    assert staged[0]["user_id"] == "user-1"
    assert staged[0]["workspace_id"] == "workspace-1"
