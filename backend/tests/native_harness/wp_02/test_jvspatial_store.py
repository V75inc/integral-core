"""Persistence contract tests for the Core-owned Harness StepStore."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai_harness.planning import PlanItem, TaskStatus
from pydantic_ai_harness.step_persistence import (
    ContinuableSnapshot,
    RunRecord,
    StepEvent,
    ToolEffectRecord,
)

from app.agentive.harness.contracts import HarnessExecutionScope
from app.agentive.harness.jvspatial_store import JvSpatialStepStore
from app.agentive.harness.plan_store import JvSpatialPlanStore
from app.agentive.harness.scoped_store import ScopedStepStore
from app.models.harness_records import (
    HarnessEventRecord,
    HarnessRunRecord,
    HarnessSnapshotRecord,
    HarnessToolEffectRecord,
)

RECORD_MODELS = (
    HarnessRunRecord,
    HarnessEventRecord,
    HarnessSnapshotRecord,
    HarnessToolEffectRecord,
)


def _scope(tenant: str, principal: str) -> HarnessExecutionScope:
    return HarnessExecutionScope(
        tenant_id=tenant,
        principal_id=principal,
        workspace_id=tenant,
        thread_id="thread-1",
        session_id="session-1",
        run_id="run-1",
        permission_revision="permissions-1",
        capability_version="capabilities-1",
    )


@pytest.fixture
def persisted_objects(monkeypatch: pytest.MonkeyPatch) -> dict[type, dict[str, object]]:
    """Provide a shared fake Object database while retaining model validation."""
    rows: dict[type, dict[str, object]] = {model: {} for model in RECORD_MODELS}
    monkeypatch.setattr(
        "app.services.credential_crypto._resolve_key", lambda: (b"k" * 32, None)
    )

    for model in RECORD_MODELS:

        async def create_if_absent(cls, **kwargs):
            current = rows[cls].get(kwargs["id"])
            if current is not None:
                return current, False
            current = cls(**kwargs)
            rows[cls][current.id] = current
            return current, True

        async def find(cls, query=None, **kwargs):
            filters = {**(query or {}), **kwargs}
            return [
                row
                for row in rows[cls].values()
                if all(getattr(row, key) == value for key, value in filters.items())
            ]

        monkeypatch.setattr(model, "create_if_absent", classmethod(create_if_absent))
        monkeypatch.setattr(model, "find", classmethod(find))
    return rows


@pytest.mark.asyncio
async def test_persists_encrypted_history_and_tool_effect_transitions(
    persisted_objects: dict[type, dict[str, object]],
) -> None:
    """Round-trip encrypted records and fold append-only tool transitions."""
    scope = _scope("workspace-a", "user-a")
    backend = JvSpatialStepStore(scope=scope)
    store = ScopedStepStore(store=backend, scope=scope)
    now = datetime.now(timezone.utc)

    await store.register_run(
        RunRecord(
            run_id="run-1",
            conversation_id="session-1",
            agent_name="integral",
        )
    )
    await store.append_event(
        StepEvent(
            run_id="run-1",
            conversation_id="session-1",
            kind="run_started",
            step_index=0,
            timestamp=now,
            idempotency_key="event-0",
        )
    )
    snapshot = ContinuableSnapshot(
        run_id="run-1",
        conversation_id="session-1",
        step_index=1,
        timestamp=now,
        messages=[ModelRequest(parts=[UserPromptPart(content="private transcript")])],
        idempotency_key="snapshot-1",
    )
    await store.save_snapshot(snapshot)
    effect_started = ToolEffectRecord(
        run_id="run-1",
        tool_call_id="call-1",
        tool_name="integral.search",
        status="started",
        started_at=now,
        idempotency_key="effect-1",
    )
    await store.record_tool_effect(effect_started)

    later = now + timedelta(microseconds=1)
    await store.record_tool_effect(
        ToolEffectRecord(
            run_id="run-1",
            tool_call_id="call-1",
            tool_name="integral.search",
            status="completed",
            started_at=now,
            ended_at=later,
            idempotency_key="effect-1",
            effect_summary="2 results",
        )
    )

    restored_run = await store.get_run(run_id="run-1")
    assert restored_run is not None
    assert restored_run.conversation_id == "session-1"
    assert (await store.list_events(run_id="run-1"))[0].idempotency_key == "event-0"
    restored_snapshot = await store.latest_snapshot(run_id="run-1")
    assert restored_snapshot is not None
    assert restored_snapshot.messages == snapshot.messages
    assert await store.list_unresolved_tool_effects(run_id="run-1") == []
    effect = await store.get_tool_effect(run_id="run-1", tool_call_id="call-1")
    assert effect is not None and effect.status == "completed"

    stored_payloads = [
        row.payload_ciphertext
        for model_rows in persisted_objects.values()
        for row in model_rows.values()
    ]
    assert stored_payloads
    assert all(value.startswith("v1:") for value in stored_payloads)
    assert all("private transcript" not in value for value in stored_payloads)


@pytest.mark.asyncio
async def test_persistence_queries_are_isolated_by_execution_scope(
    persisted_objects: dict[type, dict[str, object]],
) -> None:
    """A shared Object collection must never expose another scope's rows."""
    scope_a = _scope("workspace-a", "user-a")
    scope_b = _scope("workspace-b", "user-b")
    scope_c = _scope("workspace-a", "user-c")
    store_a = ScopedStepStore(store=JvSpatialStepStore(scope=scope_a), scope=scope_a)
    store_b = ScopedStepStore(store=JvSpatialStepStore(scope=scope_b), scope=scope_b)
    store_c = ScopedStepStore(store=JvSpatialStepStore(scope=scope_c), scope=scope_c)

    await store_a.register_run(RunRecord(run_id="run-1", conversation_id="session-1"))
    await store_a.save_snapshot(
        ContinuableSnapshot(
            run_id="run-1",
            conversation_id="session-1",
            step_index=1,
            messages=[ModelRequest(parts=[UserPromptPart(content="tenant A")])],
        )
    )

    assert await store_b.get_run(run_id="run-1") is None
    assert await store_b.latest_snapshot(run_id="run-1") is None
    assert await store_b.list_events(run_id="run-1") == []
    assert await store_b.get_tool_effect(run_id="run-1", tool_call_id="call-1") is None
    assert await store_c.get_run(run_id="run-1") is None
    assert await store_c.latest_snapshot(run_id="run-1") is None


@pytest.mark.asyncio
async def test_jvspatial_object_backend_round_trips_real_database_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The adapter persists through Integral's active jvspatial context."""
    monkeypatch.setattr(
        "app.services.credential_crypto._resolve_key", lambda: (b"k" * 32, None)
    )
    tenant_id = f"workspace-{uuid4()}"
    scope = _scope(tenant_id, f"user-{uuid4()}")
    store = ScopedStepStore(store=JvSpatialStepStore(scope=scope), scope=scope)
    now = datetime.now(timezone.utc)
    messages = [ModelRequest(parts=[UserPromptPart(content="restart proof")])]
    await store.register_run(
        RunRecord(
            run_id=scope.framework_run_id,
            conversation_id=scope.framework_conversation_id,
            started_at=now,
        )
    )
    await store.save_snapshot(
        ContinuableSnapshot(
            run_id=scope.framework_run_id,
            conversation_id=scope.framework_conversation_id,
            step_index=3,
            messages=messages,
            timestamp=now,
            idempotency_key="checkpoint-3",
        )
    )

    restored = await store.latest_snapshot(run_id=scope.framework_run_id)
    assert restored is not None
    assert restored.step_index == 3
    assert restored.messages == messages
    assert (await store.get_run(run_id=scope.framework_run_id)) is not None


@pytest.mark.asyncio
async def test_scoped_plan_store_round_trips_encrypted_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Planning state persists between scoped store instances without leakage."""
    monkeypatch.setattr(
        "app.services.credential_crypto._resolve_key", lambda: (b"k" * 32, None)
    )
    tenant_id = f"workspace-{uuid4()}"
    scope_a = _scope(tenant_id, f"user-{uuid4()}")
    scope_b = _scope(tenant_id, f"user-{uuid4()}")
    store_a = JvSpatialPlanStore(scope=scope_a)
    store_a_again = JvSpatialPlanStore(scope=scope_a)
    store_b = JvSpatialPlanStore(scope=scope_b)
    item = PlanItem(id="step-1", content="Persist a scoped plan")

    created = await store_a.add_item(item)
    assert created.id == "step-1"
    updated = await store_a_again.update_item("step-1", status=TaskStatus.in_progress)
    assert updated is not None and updated.status is TaskStatus.in_progress
    assert (await store_a.get_items())[0].content == "Persist a scoped plan"
    assert await store_b.get_items() == []
    assert await store_a_again.remove_item("step-1") is True
    assert await store_a.get_items() == []
