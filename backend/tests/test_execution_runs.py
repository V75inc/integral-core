"""Harness Kernel run lifecycle tests."""

from __future__ import annotations

from typing import Any

import pytest

from app.agentive.services import execution_runs


class _Run:
    def __init__(self: "_Run", **fields: Any) -> None:
        self.__dict__.update(fields)
        self.saved = 0

    async def save(self: "_Run") -> None:
        self.saved += 1


@pytest.mark.asyncio
async def test_start_run_records_integral_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A created run records the tenant and provider execution boundary."""
    captured: dict[str, Any] = {}

    async def create(**fields: Any) -> _Run:
        captured.update(fields)
        return _Run(**fields)

    async def snapshot(_workspace_id: str) -> dict[str, Any]:
        return {
            "manifest_version": "1.0.0",
            "fingerprint": "snapshot-fingerprint",
            "capabilities": [],
            "apps": [],
            "environments": [],
            "unresolved_apps": [],
        }

    monkeypatch.setattr(execution_runs.AgentRun, "create", create)
    monkeypatch.setattr(execution_runs, "build_capability_snapshot", snapshot)
    run = await execution_runs.start_run(
        thread_id="thread-1",
        user_id="user-1",
        workspace_id="workspace-1",
        provider_id="jvagent",
        agent_id="agent-1",
    )

    assert run.status == "running"
    assert captured["thread_id"] == "thread-1"
    assert captured["workspace_id"] == "workspace-1"
    assert captured["provider_id"] == "jvagent"
    recorded_snapshot = captured["capability_snapshot"]
    assert recorded_snapshot["fingerprint"] == "snapshot-fingerprint"
    assert captured["run_id"]


@pytest.mark.asyncio
async def test_finish_run_is_idempotent_for_terminal_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retry cannot overwrite an already terminal run."""
    run = _Run(status="running", run_id="run-1")

    async def find_one(_query: dict[str, str]) -> _Run:
        return run

    monkeypatch.setattr(execution_runs.AgentRun, "find_one", find_one)
    result = await execution_runs.finish_run("run-1", status="succeeded")
    assert result is run
    assert run.status == "succeeded"
    assert run.saved == 1

    result = await execution_runs.finish_run("run-1", status="failed")
    assert result is run
    assert run.status == "succeeded"
    assert run.saved == 1


@pytest.mark.asyncio
async def test_provider_tool_events_update_one_redacted_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider tool boundaries are a single receipt without raw payloads."""
    created: list[_Run] = []

    async def find_one(_query: dict[str, str]) -> _Run | None:
        return created[0] if created else None

    async def create(**fields: Any) -> _Run:
        step = _Run(**fields)
        created.append(step)
        return step

    monkeypatch.setattr(execution_runs.RunStep, "find_one", find_one)
    monkeypatch.setattr(execution_runs.RunStep, "create", create)

    await execution_runs.record_provider_event_step(
        "run-1",
        {
            "type": "tool-call",
            "toolCallId": "tool-1",
            "name": "integral_create_entry",
            "status": "running",
            "args": {"secret": "never persisted"},
        },
        ordinal=1,
    )
    await execution_runs.record_provider_event_step(
        "run-1",
        {
            "type": "tool-call",
            "toolCallId": "tool-1",
            "name": "integral_create_entry",
            "status": "complete",
            "result": {"id": "entry-1"},
        },
        ordinal=2,
    )

    assert len(created) == 1
    step = created[0]
    assert step.status == "succeeded"
    assert step.step_key == "tool:tool-1"
    assert step.input_fingerprint
    assert "never persisted" not in step.input_fingerprint
    assert step.output_fingerprint and "entry-1" not in step.output_fingerprint


@pytest.mark.asyncio
async def test_model_steps_accumulate_redacted_token_summary_on_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One run keeps model/version and token totals without prompt content."""
    run = _Run(
        run_id="run-model-summary",
        metadata={
            "harness": {"provider_id": "jvagent", "agent_id": "agent-1"},
            "model_observability": {
                "version": "v1",
                "models": [],
                "total_input_tokens": 0,
                "total_output_tokens": 0,
            },
        },
    )
    steps: list[_Run] = []

    async def find_run(_query: dict[str, str]) -> _Run:
        return run

    async def find_step(_query: dict[str, str]) -> None:
        return None

    async def create_step(**fields: Any) -> _Run:
        step = _Run(**fields)
        steps.append(step)
        return step

    monkeypatch.setattr(execution_runs.AgentRun, "find_one", find_run)
    monkeypatch.setattr(execution_runs.RunStep, "find_one", find_step)
    monkeypatch.setattr(execution_runs.RunStep, "create", create_step)

    await execution_runs.record_provider_event_step(
        run.run_id,
        {
            "type": "step",
            "modelId": "gpt-4.1-2025-04-14",
            "finishReason": "stop",
            "usage": {"inputTokens": 12, "outputTokens": 7, "prompt": "never save"},
        },
        ordinal=1,
    )
    await execution_runs.record_provider_event_step(
        run.run_id,
        {
            "type": "step",
            "modelId": "gpt-4.1-2025-04-14",
            "finishReason": "length",
            "usage": {"inputTokens": 5, "outputTokens": 3},
        },
        ordinal=2,
    )

    assert len(steps) == 2
    summary = run.metadata["model_observability"]
    assert summary == {
        "version": "v1",
        "models": [
            {
                "model_id": "gpt-4.1-2025-04-14",
                "calls": 2,
                "input_tokens": 17,
                "output_tokens": 10,
                "finish_reasons": ["stop", "length"],
            }
        ],
        "total_input_tokens": 17,
        "total_output_tokens": 10,
    }
    assert "never save" not in str(summary)
    assert run.saved == 2


@pytest.mark.asyncio
async def test_terminal_provider_trace_is_whitelisted_on_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Diagnostic traces explain a loop outcome without retaining raw prompts."""
    run = _Run(run_id="run-trace", metadata={})

    async def find_run(_query: dict[str, str]) -> _Run:
        return run

    monkeypatch.setattr(execution_runs.AgentRun, "find_one", find_run)

    await execution_runs.record_provider_event_step(
        run.run_id,
        {
            "type": "final-content",
            "payload": {
                "interaction": {
                    "observability_metrics": [
                        {
                            "event_type": "orchestrator_activation",
                            "data": {
                                "tool_protocol": "native",
                                "protocol_reason": "configured",
                                "tick_count": 3,
                                "budget": 12,
                                "guards": ["repeat"],
                                "ended_via": "repeat_guard",
                                "tools_invoked": ["integral_propose_design"],
                                "system_prompt": "must never persist",
                                "tool_observations": [{"secret": "no"}],
                            },
                        }
                    ]
                }
            },
        },
        ordinal=1,
    )

    assert run.metadata["provider_trace"] == {
        "tool_protocol": "native",
        "protocol_reason": "configured",
        "tick_count": 3,
        "budget": 12,
        "model_calls": 0,
        "guards": ["repeat"],
        "ended_via": "repeat_guard",
        "tools_invoked": ["integral_propose_design"],
    }
    assert "must never persist" not in str(run.metadata)
    assert "secret" not in str(run.metadata)


def test_core_capability_snapshot_is_deterministic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only dispatchable Core capabilities become a run's Core contract."""
    monkeypatch.setattr(
        "app.agentive.tooling.catalogue.build_tool_catalogue",
        lambda: [
            {"name": "integral_z", "op_class": "read", "policy_action": None},
            {
                "name": "integral_a",
                "op_class": "propose",
                "policy_action": "entry.create",
            },
        ],
    )

    snapshot = execution_runs.build_core_capability_snapshot()

    assert snapshot["manifest_version"] == "1.0.0"
    assert [item["name"] for item in snapshot["capabilities"]] == [
        "integral_a",
        "integral_z",
    ]


def test_finalized_snapshot_records_declarations_deterministically() -> None:
    """Persisted app and connector declarations participate in the contract."""
    draft = {
        "manifest_version": "1.0.0",
        "capabilities": [{"name": "integral_z", "op_class": "read"}],
        "apps": [
            {
                "app_id": "app-z",
                "package_slug": "assets",
                "package_version": "1.0.0",
                "operations": [{"key": "check_in", "kind": "execute"}],
            }
        ],
        "environments": [
            {
                "connector_id": "connector-z",
                "kind": "jvagent",
                "subclass_slug": "calendar",
                "capabilities": ["calendar.read"],
                "permissions": ["calendar.read"],
                "health_status": "healthy",
            }
        ],
        "unresolved_apps": [],
    }

    first = execution_runs._finalize_snapshot(dict(draft))
    second = execution_runs._finalize_snapshot(dict(draft))

    assert first["fingerprint"] == second["fingerprint"]
    assert len(first["fingerprint"]) == 64


@pytest.mark.asyncio
async def test_surface_run_identity_separates_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One idempotency key cannot reuse a run across capability boundaries."""
    runs: dict[str, _Run] = {}

    async def find_run(query: dict[str, Any]) -> _Run | None:
        return runs.get(str(query["run_id"]))

    async def start(**kwargs: Any) -> _Run:
        run = _Run(
            run_id=kwargs["run_id"],
            user_id=kwargs["user_id"],
            workspace_id=kwargs["workspace_id"],
            origin=kwargs["origin"],
            app_id=kwargs["app_id"],
        )
        runs[run.run_id] = run
        return run

    monkeypatch.setattr(execution_runs.AgentRun, "find_one", find_run)
    monkeypatch.setattr(execution_runs, "start_run", start)
    common = {
        "user_id": "user-1",
        "workspace_id": "workspace-1",
        "origin": "http",
        "idempotency_key": "shared-key",
    }

    query_run = await execution_runs.mint_surface_run(
        **common,
        capability_key="integral_query_spec",
        source="core",
    )
    app_run = await execution_runs.mint_surface_run(
        **common,
        capability_key="recent_open",
        source="app",
        app_id="app-1",
    )
    replay = await execution_runs.mint_surface_run(
        **common,
        capability_key="integral_query_spec",
        source="core",
    )

    assert query_run.run_id != app_run.run_id
    assert replay is query_run
