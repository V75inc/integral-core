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
        provider_id="jvagent",
        agent_id="agent-1",
        status="succeeded",
        capability_version="tool-manifest-v1",
        started_at="2026-09-22T10:00:00Z",
        finished_at="2026-09-22T10:00:01Z",
        capability_snapshot={"secret_declaration": "do-not-export"},
        metadata={
            "harness": {
                "provider_id": "jvagent",
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
        "peak_input_tokens": 0,
        "model_call_count": 2,
        "tool_call_count": 1,
        "tool_retries": 1,
    }
    assert exported["steps"][0]["name"] == "integral_create_entry"
    assert "result_json" not in exported["steps"][0]
    assert "input_fingerprint" not in exported["steps"][0]
    assert "customer_name" not in str(exported)
    assert "secret_declaration" not in str(exported)


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
                "provider_id": "jvagent",
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
