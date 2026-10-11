"""Redacted, tenant-scoped exports for live-model qualification evidence."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from app.agentive.services import execution_runs


class _Record:
    def __init__(self, **fields: Any) -> None:
        self.__dict__.update(fields)


@pytest.mark.asyncio
async def test_qualification_export_projects_only_safe_receipt_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prompts, completions, tool payloads, and snapshots never leave storage."""
    run = _Record(
        run_id="run-1",
        user_id="user-1",
        workspace_id="workspace-1",
        provider_id="integral_native",
        agent_id="agent-1",
        status="succeeded",
        capability_version="tool-manifest-v1",
        started_at="2026-09-22T10:00:00Z",
        finished_at="2026-09-22T10:00:01Z",
        capability_snapshot={"secret_declaration": "do-not-export"},
        metadata={
            "harness": {
                "provider_id": "integral_native",
                "provider_label": "Resident",
                "agent_id": "agent-1",
            },
            "model_observability": {
                "models": [
                    {
                        "model_id": "model-a",
                        "calls": 2,
                        "input_tokens": 12,
                        "output_tokens": 8,
                        "finish_reasons": ["stop"],
                    }
                ],
                "total_input_tokens": 12,
                "total_output_tokens": 8,
            },
        },
    )
    step = _Record(
        run_id="run-1",
        step_key="tool:1",
        kind="tool",
        name="integral_create_entry",
        status="succeeded",
        attempt=2,
        duration_ms=125.0,
        error_code=None,
        policy_decision="allowed",
        denial_code=None,
        approval_ref="approval-1",
        result_class="applied",
        snapshot_divergence=False,
        input_fingerprint="input-hash",
        output_fingerprint="output-hash",
        result_json='{"customer_name":"private"}',
    )

    async def find_run(_query: dict[str, str]) -> _Record:
        return run

    async def find_steps(_query: dict[str, str]) -> list[_Record]:
        return [step]

    monkeypatch.setattr(execution_runs.AgentRun, "find_one", find_run)
    monkeypatch.setattr(execution_runs.RunStep, "find", find_steps)

    exported = await execution_runs.export_qualification_run(
        "run-1", user_id="user-1", workspace_id="workspace-1"
    )

    assert exported is not None
    assert exported["metrics"] == {
        "latency_ms": 1000.0,
        "input_tokens": 12,
        "output_tokens": 8,
        "model_call_count": 2,
        "tool_call_count": 1,
        "tool_retries": 1,
    }
    assert exported["steps"][0]["name"] == "integral_create_entry"
    assert "result_json" not in exported["steps"][0]
    assert "input_fingerprint" not in exported["steps"][0]
    assert "customer_name" not in str(exported)
    assert "secret_declaration" not in str(exported)
    from app.schemas.api.ai_chat import QualificationRunExport

    public = QualificationRunExport.model_validate(exported).model_dump(mode="json")
    assert public["context_requests"] == []
    assert public["observed_usage"] is None


@pytest.mark.asyncio
async def test_qualification_export_rejects_other_user_or_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _Record(run_id="run-1", user_id="owner", workspace_id="workspace-1")

    async def find_run(_query: dict[str, str]) -> _Record:
        return run

    monkeypatch.setattr(execution_runs.AgentRun, "find_one", find_run)

    assert (
        await execution_runs.export_qualification_run(
            "run-1", user_id="other", workspace_id="workspace-1"
        )
        is None
    )
    assert (
        await execution_runs.export_qualification_run(
            "run-1", user_id="owner", workspace_id="workspace-2"
        )
        is None
    )


@pytest.mark.asyncio
async def test_qualification_export_endpoint_is_scope_bound(
    authenticated_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_response = await authenticated_client.post(
        "/api/workspaces", json={"name": "Qualification evidence workspace"}
    )
    assert workspace_response.status_code == 200, workspace_response.text
    workspace_id = workspace_response.json()["workspace"]["id"]
    headers = {"X-Integral-Scope": f"ws:{workspace_id}"}

    async def fake_export(
        run_id: str, *, user_id: str, workspace_id: str
    ) -> dict[str, Any]:
        assert run_id == "run-1"
        assert user_id
        return {
            "run_id": run_id,
            "status": "succeeded",
            "provider_configuration": {
                "provider_id": "integral_native",
                "provider_label": "Resident",
                "agent_id": "",
                "capability_version": "tool-manifest-v1",
            },
            "metrics": {
                "latency_ms": 10.0,
                "input_tokens": 1,
                "output_tokens": 1,
                "model_call_count": 1,
                "tool_call_count": 0,
                "tool_retries": 0,
            },
            "models": [],
            "redacted_trace_ref": "agent-run:run-1",
            "steps": [],
        }

    monkeypatch.setattr(execution_runs, "export_qualification_run", fake_export)
    response = await authenticated_client.get(
        "/api/chat/runs/run-1/qualification-export", headers=headers
    )

    assert response.status_code == 200, response.text
    assert response.json()["redacted_trace_ref"] == "agent-run:run-1"


@pytest.mark.asyncio
async def test_qualification_endpoint_accepts_actual_exporter_shape(
    authenticated_client: AsyncClient,
    test_user: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the real projection and route schema, not a legacy fake shape."""
    from app.agentive.harness import model_observations

    created = await authenticated_client.post(
        "/api/workspaces", json={"name": "Actual qualification receipt"}
    )
    assert created.status_code == 200, created.text
    workspace_id = created.json()["workspace"]["id"]
    run = _Record(
        run_id="run-actual",
        user_id=test_user.user_id,
        workspace_id=workspace_id,
        thread_id="thread-actual",
        status="succeeded",
        metadata={},
    )

    async def find_run(query):
        return run

    async def no_steps(query):
        return []

    async def no_observations(**identity):
        return []

    monkeypatch.setattr(execution_runs.AgentRun, "find_one", find_run)
    monkeypatch.setattr(execution_runs.RunStep, "find", no_steps)
    monkeypatch.setattr(
        model_observations, "list_run_model_request_observations", no_observations
    )
    response = await authenticated_client.get(
        "/api/chat/runs/run-actual/qualification-export",
        headers={"X-Integral-Scope": f"ws:{workspace_id}"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["context_requests"] == []
    assert response.json()["observed_usage"] is None
    original_owner = run.user_id
    run.user_id = "another-owner"
    denied = await authenticated_client.get(
        "/api/chat/runs/run-actual/qualification-export",
        headers={"X-Integral-Scope": f"ws:{workspace_id}"},
    )
    assert denied.status_code == 404, denied.text
    run.user_id = original_owner
    other = await authenticated_client.post(
        "/api/workspaces", json={"name": "Other qualification scope"}
    )
    assert other.status_code == 200, other.text
    other_workspace = other.json()["workspace"]["id"]
    denied = await authenticated_client.get(
        "/api/chat/runs/run-actual/qualification-export",
        headers={"X-Integral-Scope": f"ws:{other_workspace}"},
    )
    assert denied.status_code == 404, denied.text


@pytest.mark.asyncio
async def test_cancelled_qualification_reads_known_usage_and_cost_from_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import datetime, timezone
    from decimal import Decimal

    from app.agentive.harness import model_observations
    from app.agentive.harness.contracts import (
        HarnessExecutionScope,
        ModelRequestContextObservation,
        ModelUsageObservation,
        PhysicalModelRequest,
    )

    run = _Record(
        run_id="run-1",
        user_id="user-1",
        workspace_id="workspace-1",
        thread_id="thread-1",
        provider_id="integral_native",
        status="cancelled",
        metadata={"model_observability": {"total_input_tokens": 0}},
    )
    scope = HarnessExecutionScope(
        tenant_id="workspace-1",
        principal_id="user-1",
        workspace_id="workspace-1",
        thread_id="thread-1",
        session_id="original-session",
        run_id="run-1",
        permission_revision="p1",
        capability_version="v1",
    )
    intent = PhysicalModelRequest(
        request_id="request-1",
        scope=scope,
        provider="openai",
        model="gpt-4.1",
        attempt=1,
        dispatched_at=datetime.now(timezone.utc),
        outcome="dispatch_intent",
        request_context=ModelRequestContextObservation(
            instruction_chars=100,
            conversation_chars=200,
            tool_result_chars=25,
            tool_schema_chars=300,
            message_count=4,
            visible_tool_count=2,
            tool_schema_fingerprint="schema-hash",
        ),
    )
    cancelled = intent.model_copy(
        update={
            "outcome": "cancelled",
            "observed_at": datetime.now(timezone.utc),
            "usage": ModelUsageObservation(
                input_tokens=11,
                output_tokens=2,
                provider_cost_usd=Decimal("0.00001234"),
                cost_source="litellm_response",
                complete=False,
            ),
        }
    )
    transitions = [intent, cancelled]

    async def find_run(query):
        return run

    async def find_steps(query):
        return []

    async def load(**identity):
        assert identity == dict(
            run_id="run-1",
            principal_id="user-1",
            workspace_id="workspace-1",
            thread_id="thread-1",
        )
        return transitions

    monkeypatch.setattr(execution_runs.AgentRun, "find_one", find_run)
    monkeypatch.setattr(execution_runs.RunStep, "find", find_steps)
    monkeypatch.setattr(model_observations, "list_run_model_request_observations", load)
    receipt = await execution_runs.export_qualification_run(
        "run-1",
        user_id="user-1",
        workspace_id="workspace-1",
    )
    assert receipt["metrics"]["input_tokens"] == 11
    assert receipt["metrics"]["model_call_count"] == 1
    assert receipt["observed_usage"]["provider_cost_usd"] == "0.00001234"
    assert receipt["observed_usage"]["token_usage_complete"] is False
    assert receipt["observed_usage"]["unresolved_request_count"] == 1
    from app.schemas.api.ai_chat import QualificationRunExport

    public = QualificationRunExport.model_validate(receipt).model_dump(mode="json")
    assert public["observed_usage"]["provider_cost_usd"] == "0.00001234"
    assert public["observed_usage"]["provider_cost_complete"] is False
    assert public["context_requests"][0]["instruction_chars"] == 100
    assert public["context_requests"][0]["tool_schema_chars"] == 300
    assert public["context_requests"][0]["repeated_tool_schema_chars"] == 0
    # A later terminal observation enriches the read without adding another billable call.
    transitions.append(
        cancelled.model_copy(
            update={
                "outcome": "responded",
                "observed_at": datetime.now(timezone.utc),
                "usage": cancelled.usage.model_copy(update={"complete": True}),
            }
        )
    )
    receipt = await execution_runs.export_qualification_run(
        "run-1",
        user_id="user-1",
        workspace_id="workspace-1",
    )
    assert receipt["metrics"]["model_call_count"] == 1
    assert receipt["observed_usage"]["token_usage_complete"] is True
    assert receipt["observed_usage"]["provider_cost_complete"] is True
    assert receipt["observed_usage"]["provider_cost_usd"] == "0.00001234"


@pytest.mark.parametrize(
    "location", ["export", "context", "request_usage", "run_usage"]
)
def test_qualification_schema_refuses_unrecognized_content_fields(location):
    """Adding accounting metadata must not open arbitrary content projections."""
    from pydantic import ValidationError

    from app.schemas.api.ai_chat import QualificationRunExport

    payload = {
        "run_id": "run-1",
        "status": "succeeded",
        "provider_configuration": {},
        "metrics": {},
        "models": [],
        "redacted_trace_ref": "agent-run:run-1",
        "steps": [],
        "context_requests": [
            {
                "request_id": "request-1",
                "model": "example-model",
                "attempt": 1,
                "outcome": "responded",
                "usage": {"cost_source": "unavailable", "complete": False},
            }
        ],
        "observed_usage": {
            "request_count": 1,
            "completed_request_count": 1,
            "unresolved_request_count": 0,
            "reported_input_tokens": 0,
            "reported_output_tokens": 0,
            "token_usage_complete": False,
            "provider_cost_complete": False,
        },
    }
    public = QualificationRunExport.model_validate(payload).model_dump(mode="json")
    assert public["observed_usage"]["provider_cost_usd"] is None
    assert public["context_requests"][0]["usage"]["provider_cost_usd"] is None
    assert public["context_requests"][0]["instruction_chars"] is None
    target = {
        "export": payload,
        "context": payload["context_requests"][0],
        "request_usage": payload["context_requests"][0]["usage"],
        "run_usage": payload["observed_usage"],
    }[location]
    target["private_content"] = "must-not-export"
    with pytest.raises(ValidationError, match="private_content"):
        QualificationRunExport.model_validate(payload)
