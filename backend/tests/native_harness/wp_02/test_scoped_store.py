"""Tenant/session isolation tests against one deliberately shared store."""

import pytest
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai_harness.step_persistence import (
    ContinuableSnapshot,
    InMemoryStepStore,
    RunRecord,
    StepEvent,
    ToolEffectRecord,
)

from app.agentive.harness.contracts import HarnessExecutionScope
from app.agentive.harness.scoped_store import HarnessScopeViolation, ScopedStepStore


def _scope(
    *, tenant_id: str, principal_id: str, thread_id: str, session_id: str
) -> HarnessExecutionScope:
    return HarnessExecutionScope(
        tenant_id=tenant_id,
        principal_id=principal_id,
        workspace_id=tenant_id,
        thread_id=thread_id,
        session_id=session_id,
        run_id="run-current",
        permission_revision="permissions-1",
        capability_version="capabilities-1",
    )


@pytest.mark.asyncio
async def test_all_step_store_records_are_isolated_by_core_scope() -> None:
    """A shared backing store cannot expose one tenant/session to another."""
    backing = InMemoryStepStore()
    scope_a = _scope(
        tenant_id="tenant-a",
        principal_id="user-a",
        thread_id="thread-a",
        session_id="session-a",
    )
    scope_b = _scope(
        tenant_id="tenant-b",
        principal_id="user-b",
        thread_id="thread-b",
        session_id="session-b",
    )
    scope_c = _scope(
        tenant_id="tenant-a",
        principal_id="user-c",
        thread_id="thread-c",
        session_id="session-c",
    )
    store_a = ScopedStepStore(store=backing, scope=scope_a)
    store_b = ScopedStepStore(store=backing, scope=scope_b)
    store_c = ScopedStepStore(store=backing, scope=scope_c)
    run_id = "run-1"

    await store_a.register_run(
        RunRecord(run_id=run_id, conversation_id=scope_a.session_id)
    )
    await store_a.append_event(
        StepEvent(
            run_id=run_id,
            kind="run_started",
            step_index=0,
            conversation_id=scope_a.session_id,
            tool_call_id="tool-call-1",
            idempotency_key="event-1",
        )
    )
    await store_a.save_snapshot(
        ContinuableSnapshot(
            run_id=run_id,
            step_index=1,
            conversation_id=scope_a.session_id,
            messages=[ModelRequest(parts=[UserPromptPart("private transcript A")])],
            idempotency_key="snapshot-1",
        )
    )
    await store_a.record_tool_effect(
        ToolEffectRecord(
            run_id=run_id,
            tool_call_id="tool-call-1",
            tool_name="integral_query",
            status="started",
            idempotency_key="effect-1",
        )
    )

    assert (await store_a.get_run(run_id=run_id)).run_id == run_id
    assert len(await store_a.list_runs()) == 1
    assert len(await store_a.list_events(run_id=run_id)) == 1
    assert (await store_a.latest_snapshot(run_id=run_id)).run_id == run_id
    assert len(await store_a.list_snapshots(run_id=run_id)) == 1
    assert (
        await store_a.get_tool_effect(run_id=run_id, tool_call_id="tool-call-1")
    ).tool_call_id == "tool-call-1"
    assert len(await store_a.list_unresolved_tool_effects(run_id=run_id)) == 1

    assert await store_b.get_run(run_id=run_id) is None
    assert await store_b.list_runs() == []
    assert await store_b.list_events(run_id=run_id) == []
    assert await store_b.latest_snapshot(run_id=run_id) is None
    assert await store_b.list_snapshots(run_id=run_id) == []
    assert (
        await store_b.get_tool_effect(run_id=run_id, tool_call_id="tool-call-1")
    ) is None
    assert await store_b.list_unresolved_tool_effects(run_id=run_id) == []
    assert await store_c.get_run(run_id=run_id) is None
    assert await store_c.list_runs() == []
    assert await store_c.list_events(run_id=run_id) == []
    assert await store_c.latest_snapshot(run_id=run_id) is None
    assert await store_c.list_snapshots(run_id=run_id) == []
    assert (
        await store_c.get_tool_effect(run_id=run_id, tool_call_id="tool-call-1")
    ) is None
    assert await store_c.list_unresolved_tool_effects(run_id=run_id) == []
    with pytest.raises(HarnessScopeViolation, match="outside the Integral session"):
        await store_b.list_runs(conversation_id=scope_a.session_id)


@pytest.mark.asyncio
async def test_store_rejects_foreign_conversation_writes() -> None:
    """Writes that claim another session are rejected before delegation."""
    store = ScopedStepStore(
        store=InMemoryStepStore(),
        scope=_scope(
            tenant_id="tenant-a",
            principal_id="user-a",
            thread_id="thread-a",
            session_id="session-a",
        ),
    )
    with pytest.raises(HarnessScopeViolation, match="different conversation"):
        await store.register_run(
            RunRecord(run_id="run-foreign", conversation_id="session-b")
        )
