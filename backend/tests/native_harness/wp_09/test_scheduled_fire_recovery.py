"""Independent scheduled fires must not replay an earlier failed execution."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic_ai_harness.step_persistence import ToolEffectRecord

from app.agentive.harness.contracts import HarnessExecutionScope
from app.agentive.services.execution_runs import RunStep
from app.agentive.work_models import WorkApproval
from app.api.errors import ResourceConflictError
from app.schemas.agentive.work import WorkError
from app.services.chat_providers.pydantic_ai_provider import _resume_history


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "new_fire",
        "same_item",
        "other_routine",
        "previous_live",
        "foreign_scope",
        "unleased",
        "tool_effect",
        "approval",
        "broker_receipt",
    ],
)
async def test_scheduled_fire_after_empty_model_response(monkeypatch, case):
    """Only a new, currently admitted, effect-free fire can discard old history."""
    from app.agentive.harness import model_observations
    from app.agentive.services import work_execution, work_items

    scope = HarnessExecutionScope(
        tenant_id="workspace-a",
        principal_id="user-a",
        workspace_id="workspace-a",
        thread_id="thread-a",
        session_id="session-a",
        run_id="new-run",
        permission_revision="permissions-v1",
        capability_version="tools-v1",
    )
    previous_run = SimpleNamespace(
        run_id="old-run",
        status="running" if case == "previous_live" else "failed",
        user_id="user-a",
        workspace_id="other" if case == "foreign_scope" else "workspace-a",
        thread_id="thread-a",
        provider_id="integral_native",
        work_item_id="old-item",
        capability_snapshot={
            "capabilities": [{"name": "write_tool", "op_class": "write"}]
        },
    )
    context = SimpleNamespace(
        run_id="new-run",
        principal_id="user-a",
        workspace_id="workspace-a",
        thread_id="thread-a",
        work_item_id="old-item" if case == "same_item" else "new-item",
    )
    current = SimpleNamespace(
        kind="routine_turn", input_payload={"routine_id": "routine-a"}
    )
    previous = SimpleNamespace(
        kind="routine_turn",
        status="running" if case == "previous_live" else "failed",
        principal_id="user-a",
        workspace_id="workspace-a",
        thread_id="thread-a",
        input_payload={
            "routine_id": "other" if case == "other_routine" else "routine-a"
        },
    )
    now = datetime.now(timezone.utc)
    observation = SimpleNamespace(
        request_id="old-request",
        outcome="outcome_unknown",
        observed_at=now,
        dispatched_at=now,
    )
    effect = ToolEffectRecord(
        run_id="old-run",
        tool_name="write_tool",
        tool_call_id="old-call",
        status="started",
    )
    approval = SimpleNamespace(status="pending")
    receipt = SimpleNamespace(
        capability_key="write_tool",
        principal_id="user-a",
        workspace_id="workspace-a",
        status="started",
        idempotency_key="old-receipt",
    )

    async def observations(**kwargs):
        return [observation]

    async def approvals(query):
        return [approval] if case == "approval" else []

    async def steps(query):
        return [receipt] if case == "broker_receipt" else []

    async def find_previous(query):
        return previous

    async def admit(ctx):
        assert ctx is context
        if case == "unleased":
            raise WorkError("work.lease_lost", "test expired lease")
        return current

    async def no_fenced_reconciliation(_scope, run):
        return run

    class Store:
        async def list_unresolved_tool_effects(self, **kwargs):
            return [effect] if case == "tool_effect" else []

        async def list_tool_effects(self, **kwargs):
            return [effect] if case == "tool_effect" else []

        async def latest_snapshot(self, **kwargs):
            return None

    monkeypatch.setattr(
        model_observations, "list_model_request_observations", observations
    )
    monkeypatch.setattr(WorkApproval, "find", approvals)
    monkeypatch.setattr(RunStep, "find", steps)
    monkeypatch.setattr(work_items, "_current_work_item", find_previous)
    monkeypatch.setattr(work_execution, "assert_effect_boundary_allowed", admit)
    monkeypatch.setattr(
        "app.services.chat_providers.pydantic_ai_provider._reconcile_fenced_harness_run",
        no_fenced_reconciliation,
    )
    recovery_state = {}

    async def resume():
        return await _resume_history(
            scope,
            SimpleNamespace(last_run_id="old-run"),
            Store(),
            previous_run=previous_run,
            scheduled_work_context=context,
            recovery_state=recovery_state,
        )

    if case == "new_fire":
        assert await resume() == []
        assert recovery_state == {"fresh_scheduled_fire": True}
    else:
        with pytest.raises((ResourceConflictError, WorkError)):
            await resume()
        assert recovery_state == {}
    assert observation.outcome == "outcome_unknown"
    assert effect.status == "started"
    assert approval.status == "pending"
