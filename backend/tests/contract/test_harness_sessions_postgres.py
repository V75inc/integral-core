"""PostgreSQL probes for native Harness session graph transactions."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

import pytest
from jvspatial.core.context import GraphContext, set_default_context
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai_harness.planning import PlanItem
from pydantic_ai_harness.step_persistence import (
    ContinuableSnapshot,
    RunRecord,
    StepEvent,
    ToolEffectRecord,
)

from app.agentive.harness.checkpoint_manifests import persist_checkpoint_manifest
from app.agentive.harness.contracts import (
    HarnessCheckpointManifest,
    HarnessExecutionScope,
)
from app.agentive.harness.jvspatial_store import (
    HarnessPersistenceError,
    JvSpatialStepStore,
)
from app.agentive.harness.plan_store import JvSpatialPlanStore
from app.agentive.harness.scoped_store import ScopedStepStore
from app.api.errors import InsufficientPermissionsError, ResourceConflictError
from app.models.edges import HAS_HARNESS_SESSION, IS_MEMBER_OF
from app.models.harness_records import (
    HarnessCheckpointManifestRecord,
    HarnessEventRecord,
    HarnessRunRecord,
    HarnessSnapshotRecord,
    HarnessTurnInputRecord,
)
from app.models.nodes import ChatThread, HarnessSession
from app.services.chat_threads import (
    clear_provider_session,
    create_thread,
    touch_last_message,
    update_provider_session,
)
from app.services.harness_sessions import (
    advance_harness_checkpoint_pointer,
    claim_harness_run,
    ensure_harness_session,
    export_harness_session,
    purge_expired_harness_sessions,
    purge_harness_sessions_for_thread,
    transition_harness_session,
)
from tests.fixtures.workspaces import make_org_workspace


@pytest.fixture
def postgres_graph_context(postgres_raw_db):
    """Bind graph writes to the isolated PostgreSQL test database."""
    from jvspatial.core.context import _default_context_var

    token = set_default_context(GraphContext(database=postgres_raw_db))
    try:
        yield
    finally:
        _default_context_var.reset(token)


async def _thread_and_scope() -> tuple[ChatThread, HarnessExecutionScope]:
    """Create one rooted owner/workspace/thread for a session transaction."""
    from app.services.app_graph import catalog_workspace, ensure_integral_app_graph

    await ensure_integral_app_graph(include_library=False)
    suffix = uuid.uuid4().hex
    workspace = await make_org_workspace(f"harness-session-{suffix}")
    await catalog_workspace(workspace)
    owners = await workspace.nodes(
        edge=[IS_MEMBER_OF], node=["User"], direction="in", limit=5
    )
    assert owners
    user = owners[0]
    thread = await create_thread(
        user_id=user.id,
        workspace_id=workspace.id,
        provider_id="integral_native",
    )
    scope = HarnessExecutionScope(
        tenant_id=workspace.id,
        principal_id=user.id,
        workspace_id=workspace.id,
        thread_id=thread.id,
        session_id=f"session-{suffix}",
        run_id=f"run-{suffix}",
        permission_revision="membership-r1",
        capability_version="capability-r1",
    )
    return thread, scope


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_postgres_activation_persists_session_pointer_and_typed_edge(
    postgres_graph_context,
) -> None:
    """Session node, pointer CAS, and HAS_HARNESS_SESSION edge commit together."""
    thread, scope = await _thread_and_scope()

    session = await ensure_harness_session(scope=scope, binding_id="pydantic_ai_v1")

    persisted_thread = await ChatThread.get(thread.id)
    persisted_session = await HarnessSession.get(session.id)
    assert persisted_thread is not None
    assert persisted_session is not None
    assert persisted_thread.active_harness_session_id == session.id
    assert persisted_thread.harness_session_generation == 1
    assert persisted_session.session_id == scope.session_id
    context = await persisted_thread.get_context()
    edges = await context.find_edges_between(
        persisted_thread.id, persisted_session.id, edge_class=HAS_HARNESS_SESSION
    )
    assert len(edges) == 1

    from jvspatial.core.entities import Root

    from app.services.graph_reachability import GraphReachabilityWalker

    root = await Root.get(None)
    walker = GraphReachabilityWalker(visited_ids=set(), visited_by_class={})
    await walker.spawn(root)
    assert persisted_thread.id in walker.visited_ids
    assert persisted_session.id in walker.visited_ids


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_provider_session_updates_preserve_pointer_on_stale_thread(
    postgres_graph_context,
) -> None:
    """SSE metadata persistence must not erase Harness activation state."""
    thread, scope = await _thread_and_scope()
    session = await ensure_harness_session(scope=scope, binding_id="pydantic_ai_v1")

    # ``thread`` is intentionally the pre-activation object retained by the
    # chat SSE route. This mirrors the first-turn ordering in production:
    # Harness activation commits through a separate graph transaction, then
    # the stream persists ``provider_session_id`` using its older Node.
    assert thread.active_harness_session_id is None
    await update_provider_session(thread, scope.session_id)
    persisted = await ChatThread.get(thread.id)
    assert persisted is not None
    assert persisted.active_harness_session_id == session.id
    assert persisted.provider_session_id == scope.session_id

    # Assistant-draft persistence bumps activity using the same stale route
    # object after the Harness pointer has been committed.
    thread.active_harness_session_id = None
    await touch_last_message(thread)
    persisted = await ChatThread.get(thread.id)
    assert persisted is not None
    assert persisted.active_harness_session_id == session.id

    # The clear-session event also receives that same potentially stale route
    # object, and must leave the typed Harness pointer intact.
    await clear_provider_session(thread)
    persisted = await ChatThread.get(thread.id)
    assert persisted is not None
    assert persisted.active_harness_session_id == session.id
    assert persisted.provider_session_id is None


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_postgres_claim_persists_active_run_fence(postgres_graph_context) -> None:
    """The stream can claim its run after session activation has committed."""
    thread, scope = await _thread_and_scope()
    session = await ensure_harness_session(scope=scope, binding_id="pydantic_ai_v1")

    claimed = await claim_harness_run(scope=scope)
    persisted = await HarnessSession.get(session.id)

    assert persisted is not None
    assert persisted.last_run_id == scope.run_id
    assert claimed.last_run_id == scope.run_id
    assert thread.id == claimed.thread_id


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_owner_api_exports_scoped_harness_session(
    postgres_graph_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The user-facing export resolves owner/workspace before decrypting data."""
    from app.api import ai_chat

    thread, scope = await _thread_and_scope()
    session = await ensure_harness_session(scope=scope, binding_id="pydantic_ai_v1")

    monkeypatch.setattr(
        ai_chat, "_resolve_principal", lambda _request: (scope.principal_id, "owner")
    )

    async def resolve_thread(thread_id: str, user_id: str):
        assert thread_id == thread.id
        assert user_id == scope.principal_id
        return thread

    async def resolve_workspace(_request, user_id: str, resolved_thread):
        assert user_id == scope.principal_id
        assert resolved_thread.id == thread.id
        return scope.workspace_id

    monkeypatch.setattr(ai_chat, "_resolve_owned_thread", resolve_thread)
    monkeypatch.setattr(ai_chat, "_resolve_turn_workspace", resolve_workspace)

    payload = await ai_chat.export_harness_session_for_thread(
        request=object(), thread_id=thread.id, session_id=session.id
    )

    assert payload["session"]["session_id"] == scope.session_id
    assert payload["runs"] == []
    assert payload["plans"] == []


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_postgres_activation_rolls_back_node_and_pointer_on_edge_failure(
    postgres_graph_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An edge-write failure leaves neither an active pointer nor session node."""
    thread, scope = await _thread_and_scope()

    async def fail_connect(self, *_args: Any, **_kwargs: Any):
        raise RuntimeError("injected graph edge failure")

    monkeypatch.setattr(ChatThread, "connect", fail_connect)
    with pytest.raises(RuntimeError, match="injected graph edge failure"):
        await ensure_harness_session(scope=scope, binding_id="pydantic_ai_v1")

    persisted_thread = await ChatThread.get(thread.id)
    assert persisted_thread is not None
    assert persisted_thread.active_harness_session_id is None
    assert persisted_thread.harness_session_generation == 0
    assert await HarnessSession.find({"session_id": scope.session_id}) == []


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_postgres_concurrent_activation_allows_one_generation_winner(
    postgres_graph_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Independent transactions cannot both activate from one thread version."""
    from app.services import harness_sessions

    thread, first_scope = await _thread_and_scope()
    second_scope = first_scope.model_copy(
        update={
            "session_id": f"session-second-{uuid.uuid4().hex}",
            "run_id": f"run-second-{uuid.uuid4().hex}",
        }
    )
    entered_updates = 0
    both_at_cas = asyncio.Event()
    original_transaction_scope = harness_sessions.postgres_graph_transaction

    class CompareAndSwapBarrier:
        """Pause each transaction immediately before its thread CAS."""

        def __init__(self, transaction):
            self._transaction = transaction

        def __getattr__(self, name):
            return getattr(self._transaction, name)

        async def find_one_and_update(self, *args: Any, **kwargs: Any):
            nonlocal entered_updates
            entered_updates += 1
            if entered_updates == 2:
                both_at_cas.set()
            await asyncio.wait_for(both_at_cas.wait(), timeout=5)
            return await self._transaction.find_one_and_update(*args, **kwargs)

    @asynccontextmanager
    async def concurrent_transaction_scope():
        async with original_transaction_scope() as transaction:
            yield CompareAndSwapBarrier(transaction)

    monkeypatch.setattr(
        harness_sessions, "postgres_graph_transaction", concurrent_transaction_scope
    )

    async def activate(scope):
        try:
            result = await ensure_harness_session(
                scope=scope, binding_id="pydantic_ai_v1"
            )
            return ("activated", result)
        except ResourceConflictError as exc:
            return ("conflict", exc)

    outcomes = await asyncio.gather(activate(first_scope), activate(second_scope))

    assert [outcome[0] for outcome in outcomes].count("activated") == 1
    assert [outcome[0] for outcome in outcomes].count("conflict") == 1
    persisted_thread = await ChatThread.get(thread.id)
    assert persisted_thread is not None
    assert persisted_thread.harness_session_generation == 1
    active_id = persisted_thread.active_harness_session_id
    assert active_id is not None
    active_session = await HarnessSession.get(active_id)
    assert active_session is not None
    assert active_session.status == "active"
    assert active_session.session_id in {
        first_scope.session_id,
        second_scope.session_id,
    }
    assert await HarnessSession.find({"thread_id": thread.id, "status": "active"}) == [
        active_session
    ]


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_postgres_step_store_persists_encrypted_tenant_scoped_objects(
    postgres_graph_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Encrypted run/checkpoint Objects round-trip without crossing scopes."""
    thread, scope = await _thread_and_scope()
    monkeypatch.setattr(
        "app.services.credential_crypto._resolve_key", lambda: (b"k" * 32, None)
    )
    backend = JvSpatialStepStore(scope=scope)
    store = ScopedStepStore(store=backend, scope=scope)
    await store.register_run(
        RunRecord(run_id=scope.run_id, conversation_id=scope.session_id)
    )
    snapshot = ContinuableSnapshot(
        run_id=scope.run_id,
        conversation_id=scope.session_id,
        step_index=1,
        messages=[],
        state="complete",
        idempotency_key="checkpoint-1",
    )
    await store.save_snapshot(snapshot)

    restored = await store.latest_snapshot(run_id=scope.run_id)
    assert restored == snapshot
    physical_run_id = store._map_required(scope.run_id)
    run_rows = await HarnessRunRecord.find({"run_key": physical_run_id})
    checkpoint_rows = await HarnessSnapshotRecord.find({"run_key": physical_run_id})
    assert len(run_rows) == 1
    assert len(checkpoint_rows) == 1
    assert run_rows[0].scope_key == backend._scope_key
    assert checkpoint_rows[0].scope_key == backend._scope_key
    assert run_rows[0].payload_ciphertext.startswith("v1:")
    assert checkpoint_rows[0].payload_ciphertext.startswith("v1:")

    other_scope = scope.model_copy(
        update={"principal_id": f"other-{thread.id}", "run_id": scope.run_id}
    )
    other_store = ScopedStepStore(
        store=JvSpatialStepStore(scope=other_scope), scope=other_scope
    )
    assert await other_store.get_run(run_id=scope.run_id) is None


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_postgres_step_store_idempotency_race_and_collision_detection(
    postgres_graph_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Concurrent retries collapse to one row and key reuse cannot rewrite it."""
    thread, scope = await _thread_and_scope()
    monkeypatch.setattr(
        "app.services.credential_crypto._resolve_key", lambda: (b"k" * 32, None)
    )
    backend = JvSpatialStepStore(scope=scope)
    store = ScopedStepStore(store=backend, scope=scope)
    run = RunRecord(
        run_id=scope.run_id,
        conversation_id=scope.session_id,
        agent_name="integral",
    )
    event = StepEvent(
        run_id=scope.run_id,
        kind="run_started",
        step_index=0,
        conversation_id=scope.session_id,
        idempotency_key="event-stable",
    )
    snapshot = ContinuableSnapshot(
        run_id=scope.run_id,
        conversation_id=scope.session_id,
        step_index=1,
        messages=[],
        state="complete",
        idempotency_key="snapshot-stable",
    )

    await asyncio.gather(store.register_run(run), store.register_run(run))
    await asyncio.gather(store.append_event(event), store.append_event(event))
    await asyncio.gather(store.save_snapshot(snapshot), store.save_snapshot(snapshot))

    physical_run_id = store._map_required(scope.run_id)
    assert len(await HarnessRunRecord.find({"run_key": physical_run_id})) == 1
    assert len(await HarnessEventRecord.find({"run_key": physical_run_id})) == 1
    assert len(await HarnessSnapshotRecord.find({"run_key": physical_run_id})) == 1
    with pytest.raises(HarnessPersistenceError, match="run ID"):
        await store.register_run(replace(run, agent_name="other"))
    with pytest.raises(HarnessPersistenceError, match="event idempotency"):
        await store.append_event(replace(event, kind="different"))
    with pytest.raises(HarnessPersistenceError, match="checkpoint idempotency"):
        await store.save_snapshot(replace(snapshot, state="interrupted"))
    assert await ChatThread.get(thread.id) is not None


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_postgres_plan_store_concurrent_add_retries_cas(
    postgres_graph_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Concurrent plan additions retry PostgreSQL revision CAS without loss."""
    from pydantic_ai_harness.planning import PlanItem

    from app.agentive.harness.plan_store import JvSpatialPlanStore

    _, scope = await _thread_and_scope()
    monkeypatch.setattr(
        "app.services.credential_crypto._resolve_key", lambda: (b"k" * 32, None)
    )
    stores = [JvSpatialPlanStore(scope=scope), JvSpatialPlanStore(scope=scope)]
    release_reads = asyncio.Event()
    synchronized_reads = 0
    original_read = JvSpatialPlanStore._read

    async def synchronize_initial_read(instance: Any) -> Any:
        nonlocal synchronized_reads
        result = await original_read(instance)
        if synchronized_reads < 2:
            synchronized_reads += 1
            if synchronized_reads == 2:
                release_reads.set()
            await asyncio.wait_for(release_reads.wait(), timeout=5)
        return result

    monkeypatch.setattr(JvSpatialPlanStore, "_read", synchronize_initial_read)
    items = [
        PlanItem(id="step-a", content="first plan item"),
        PlanItem(id="step-b", content="second plan item"),
    ]
    await asyncio.gather(
        stores[0].add_item(items[0]),
        stores[1].add_item(items[1]),
    )

    restored = await JvSpatialPlanStore(scope=scope).get_items()
    assert {item.id for item in restored} == {"step-a", "step-b"}


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_postgres_hard_delete_purges_framework_state_and_session_node(
    postgres_graph_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Thread deletion removes private Harness payloads and graph sessions."""
    thread, scope = await _thread_and_scope()
    session = await ensure_harness_session(scope=scope, binding_id="pydantic_ai_v1")
    monkeypatch.setattr(
        "app.services.credential_crypto._resolve_key", lambda: (b"k" * 32, None)
    )
    from app.agentive.harness.turn_input import persist_turn_input_capsule
    from app.schemas.agentive.work import ChatTurnExecutionContext

    capsule_ref = await persist_turn_input_capsule(
        principal_id=scope.principal_id,
        workspace_id=scope.workspace_id,
        thread_id=thread.id,
        work_item_id="pending-work-item",
        accepted_message_id="accepted-message",
        client_request_id="request-hard-delete",
        execution_context=ChatTurnExecutionContext(system_context="private"),
        retention_days=90,
    )
    store = ScopedStepStore(store=JvSpatialStepStore(scope=scope), scope=scope)
    await store.register_run(
        RunRecord(run_id=scope.run_id, conversation_id=scope.session_id)
    )
    await store.append_event(
        StepEvent(
            run_id=scope.run_id,
            kind="run_started",
            step_index=0,
            conversation_id=scope.session_id,
            idempotency_key="event-1",
        )
    )
    await store.save_snapshot(
        ContinuableSnapshot(
            run_id=scope.run_id,
            conversation_id=scope.session_id,
            step_index=1,
            messages=[],
            state="complete",
            idempotency_key="checkpoint-1",
        )
    )
    manifest = HarnessCheckpointManifest(
        scope=scope,
        execution_fence_id=scope.run_id,
        framework_snapshot_id="checkpoint-1",
        snapshot_step_index=1,
        snapshot_state="complete",
        safe_for_resume=False,
        framework_codec_version="pydantic-ai-messages-v1",
        capability_fingerprint=scope.capability_version,
        capability_restore_policies={
            "conversation_messages": "restore",
            "brokered_tools": "rebuild",
        },
        plan_revision="plan-r1",
        tool_effect_receipt_ids=["tool-call-1:started"],
    )
    await persist_checkpoint_manifest(manifest)
    await store.record_tool_effect(
        ToolEffectRecord(
            tool_call_id="tool-call-1",
            tool_name="integral_example",
            run_id=scope.run_id,
            status="started",
            started_at=datetime.now(timezone.utc),
            idempotency_key="effect-1",
        )
    )

    foreign_scope = scope.model_copy(
        update={"principal_id": f"foreign-{scope.principal_id}"}
    )
    with pytest.raises(InsufficientPermissionsError):
        await export_harness_session(scope=foreign_scope)

    exported = await export_harness_session(scope=scope)
    assert exported["session"]["session_id"] == scope.session_id
    assert len(exported["runs"]) == 1
    assert len(exported["runs"][0]["events"]) == 1
    assert len(exported["runs"][0]["snapshots"]) == 1
    assert len(exported["runs"][0]["checkpoint_manifests"]) == 1
    assert len(exported["runs"][0]["tool_effects"]) == 1

    deleted = await purge_harness_sessions_for_thread(
        thread_id=thread.id,
        principal_id=scope.principal_id,
        workspace_id=scope.workspace_id,
    )

    assert deleted == 7  # six session records/node plus the turn input capsule
    assert await HarnessSession.find({"thread_id": thread.id}) == []
    context = await thread.get_context()
    assert (
        await context.find_edges_between(
            thread.id, session.id, edge_class=HAS_HARNESS_SESSION
        )
        == []
    )
    assert await HarnessRunRecord.find({"scope_key": store._store._scope_key}) == []
    assert await HarnessTurnInputRecord.get(capsule_ref["capsule_id"]) is None
    assert (
        await HarnessSnapshotRecord.find({"scope_key": store._store._scope_key}) == []
    )
    assert (
        await HarnessCheckpointManifestRecord.find(
            {"scope_key": store._store._scope_key}
        )
        == []
    )
    persisted_thread = await ChatThread.get(thread.id)
    assert persisted_thread is not None
    assert persisted_thread.active_harness_session_id is None
    assert persisted_thread.harness_session_generation == 1


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_postgres_retention_purges_only_old_terminal_session_payloads(
    postgres_graph_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retention removes aged terminal sessions and preserves active work."""
    from app.agentive.harness.jvspatial_store import JvSpatialStepStore

    thread, old_scope = await _thread_and_scope()
    old_session = await ensure_harness_session(
        scope=old_scope, binding_id="pydantic_ai_v1"
    )
    await transition_harness_session(scope=old_scope, status="closed")
    old_session = await HarnessSession.get(old_session.id)
    assert old_session is not None
    old_session.updated_at = "2000-01-01T00:00:00+00:00"
    await old_session.save()

    active_scope = old_scope.model_copy(
        update={
            "session_id": f"session-active-{uuid.uuid4().hex}",
            "run_id": f"run-active-{uuid.uuid4().hex}",
        }
    )
    active_session = await ensure_harness_session(
        scope=active_scope, binding_id="pydantic_ai_v1"
    )
    monkeypatch.setattr(
        "app.services.credential_crypto._resolve_key", lambda: (b"r" * 32, None)
    )
    old_store = ScopedStepStore(
        store=JvSpatialStepStore(scope=old_scope), scope=old_scope
    )
    active_store = ScopedStepStore(
        store=JvSpatialStepStore(scope=active_scope), scope=active_scope
    )
    await old_store.register_run(
        RunRecord(run_id=old_scope.run_id, conversation_id=old_scope.session_id)
    )
    await active_store.register_run(
        RunRecord(
            run_id=active_scope.run_id,
            conversation_id=active_scope.session_id,
        )
    )

    report = await purge_expired_harness_sessions(
        retention_days=90,
        batch_size=10,
        now=datetime(2026, 10, 4, tzinfo=timezone.utc),
    )

    assert report == {"sessions_deleted": 1, "records_deleted": 1}
    assert await HarnessSession.get(old_session.id) is None
    persisted_active = await HarnessSession.get(active_session.id)
    assert persisted_active is not None
    assert persisted_active.status == "active"
    assert await HarnessRunRecord.find({"scope_key": old_store._store._scope_key}) == []
    assert (
        len(await HarnessRunRecord.find({"scope_key": active_store._store._scope_key}))
        == 1
    )
    persisted_thread = await ChatThread.get(thread.id)
    assert persisted_thread is not None
    assert persisted_thread.active_harness_session_id == active_session.id
    assert persisted_thread.harness_session_generation == 2


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_postgres_checkpoint_manifest_and_run_fence(
    postgres_graph_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a persisted safe manifest for the currently fenced run can advance."""
    thread, scope = await _thread_and_scope()
    monkeypatch.setattr(
        "app.services.credential_crypto._resolve_key", lambda: (b"m" * 32, None)
    )
    session = await ensure_harness_session(scope=scope, binding_id="pydantic_ai_v1")
    session.last_run_id = scope.run_id
    from jvspatial.core.context import get_default_context

    context = get_default_context()
    await context.database.find_one_and_update(
        "node",
        {"id": session.id},
        {"$set": {"context.last_run_id": scope.run_id}},
    )

    backend = JvSpatialStepStore(scope=scope)
    await backend.save_snapshot(
        ContinuableSnapshot(
            run_id=scope.run_id,
            conversation_id=scope.session_id,
            step_index=3,
            messages=[],
            state="complete",
            idempotency_key="safe-snapshot-1",
        )
    )
    with pytest.raises(ResourceConflictError) as missing:
        await advance_harness_checkpoint_pointer(
            scope=scope, snapshot_id="safe-snapshot-1"
        )
    assert missing.value.details["reason"] == "harness_checkpoint_manifest_unavailable"

    manifest = HarnessCheckpointManifest(
        scope=scope,
        execution_fence_id=scope.run_id,
        framework_snapshot_id="safe-snapshot-1",
        snapshot_step_index=3,
        snapshot_state="complete",
        safe_for_resume=True,
        framework_codec_version="pydantic-ai-messages-v1",
        capability_fingerprint=scope.capability_version,
        capability_restore_policies={
            "conversation_messages": "restore",
            "brokered_tools": "rebuild",
        },
        plan_revision="plan-r1",
        model_request_ids=["request-r1"],
        usage_reconciled=True,
    )
    manifest_id = await persist_checkpoint_manifest(manifest)
    stored_manifest = await HarnessCheckpointManifestRecord.get(manifest_id)
    assert stored_manifest is not None
    assert stored_manifest.payload_ciphertext.startswith("v1:")

    advanced = await advance_harness_checkpoint_pointer(
        scope=scope, snapshot_id="safe-snapshot-1"
    )
    assert advanced.last_checkpoint_id == manifest_id
    assert advanced.last_checkpoint_run_id == scope.run_id

    session.last_run_id = "newer-run-fence"
    await context.database.find_one_and_update(
        "node",
        {"id": session.id},
        {"$set": {"context.last_run_id": "newer-run-fence"}},
    )
    with pytest.raises(ResourceConflictError) as stale:
        await advance_harness_checkpoint_pointer(
            scope=scope, snapshot_id="safe-snapshot-1"
        )
    assert stale.value.details["reason"] == "harness_checkpoint_fence_lost"

    persisted = await HarnessSession.get(session.id)
    assert persisted is not None
    assert persisted.last_checkpoint_id == manifest_id


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_postgres_checkpoint_key_rotation_overlap_and_new_writes(
    postgres_graph_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Checkpoint ciphertext remains readable during rotation and uses new key."""
    from app.models.harness_records import HarnessSnapshotRecord

    _, scope = await _thread_and_scope()
    keys = {"current": b"o" * 32, "previous": None}
    monkeypatch.setattr(
        "app.services.credential_crypto._resolve_key",
        lambda: (keys["current"], None),
    )
    monkeypatch.setattr(
        "app.services.credential_crypto._previous_key", lambda: keys["previous"]
    )
    store = JvSpatialStepStore(scope=scope)
    plan_store = JvSpatialPlanStore(scope=scope)
    old_snapshot = ContinuableSnapshot(
        run_id=scope.run_id,
        conversation_id=scope.session_id,
        step_index=1,
        messages=[ModelRequest(parts=[UserPromptPart(content="old encrypted state")])],
        state="complete",
        idempotency_key="old-key-snapshot",
    )
    await store.save_snapshot(old_snapshot)
    await plan_store.set_items(
        [PlanItem(id="plan-old", content="old encrypted plan state")]
    )
    manifest = HarnessCheckpointManifest(
        scope=scope,
        execution_fence_id=scope.run_id,
        framework_snapshot_id=old_snapshot.idempotency_key,
        snapshot_step_index=old_snapshot.step_index,
        snapshot_state="complete",
        safe_for_resume=True,
        framework_codec_version="pydantic-ai-messages-v1",
        capability_fingerprint=scope.capability_version,
        capability_restore_policies={"conversation_messages": "restore"},
        plan_revision="plan-revision-1",
    )
    await persist_checkpoint_manifest(manifest)
    old_row = (await HarnessSnapshotRecord.find({"run_key": scope.run_id}))[0]
    old_ciphertext = old_row.payload_ciphertext

    keys.update(current=b"n" * 32, previous=b"o" * 32)
    assert await store.latest_snapshot(run_id=scope.run_id) == old_snapshot
    new_snapshot = ContinuableSnapshot(
        run_id=scope.run_id,
        conversation_id=scope.session_id,
        step_index=2,
        messages=[ModelRequest(parts=[UserPromptPart(content="new encrypted state")])],
        state="complete",
        idempotency_key="new-key-snapshot",
    )
    await store.save_snapshot(new_snapshot)
    new_row = next(
        row
        for row in await HarnessSnapshotRecord.find({"run_key": scope.run_id})
        if row.record_key == "new-key-snapshot"
    )
    assert new_row.payload_ciphertext.startswith("v1:")
    assert new_row.payload_ciphertext != old_ciphertext

    assert await store.latest_snapshot(run_id=scope.run_id) == new_snapshot
    from app.agentive.harness.key_rotation import rewrap_harness_session_records

    report = await rewrap_harness_session_records(scope_key=store._scope_key)
    assert report["records_seen"] == 4
    assert report["records_rewrapped"] == 3
    assert report["HarnessSnapshotRecord"] == 1
    assert report["HarnessCheckpointManifestRecord"] == 1
    assert report["HarnessPlanState"] == 1
    assert await rewrap_harness_session_records(scope_key=store._scope_key) == {
        "records_seen": 4,
        "records_rewrapped": 0,
    }

    keys["previous"] = None
    assert await store.latest_snapshot(run_id=scope.run_id) == new_snapshot
    assert await plan_store.get_items() == [
        PlanItem(id="plan-old", content="old encrypted plan state")
    ]
    from app.agentive.harness.checkpoint_manifests import load_checkpoint_manifest

    assert (
        await load_checkpoint_manifest(
            scope=scope, snapshot_id=old_snapshot.idempotency_key
        )
        == manifest
    )


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_postgres_checkpoint_key_rotation_rewraps_every_harness_record(
    postgres_graph_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every encrypted Harness Object remains readable after key retirement."""
    from app.agentive.harness.key_rotation import rewrap_harness_session_records
    from app.models.harness_records import (
        HarnessEventRecord,
        HarnessModelRequestRecord,
        HarnessRunRecord,
        HarnessSnapshotRecord,
        HarnessToolEffectRecord,
        HarnessTurnInputRecord,
    )
    from app.services.credential_crypto import (
        decrypt_secret_from_storage,
        encrypt_secret_for_storage,
        rewrap_secret_for_storage,
    )

    _, scope = await _thread_and_scope()
    keys = {"current": b"r" * 32, "previous": None}
    monkeypatch.setattr(
        "app.services.credential_crypto._resolve_key",
        lambda: (keys["current"], None),
    )
    monkeypatch.setattr(
        "app.services.credential_crypto._previous_key", lambda: keys["previous"]
    )
    monkeypatch.setattr(
        "app.agentive.harness.key_rotation._current_key",
        lambda: keys["current"],
    )
    monkeypatch.setattr(
        "app.agentive.harness.key_rotation._previous_key",
        lambda: keys["previous"],
    )
    step_store = JvSpatialStepStore(scope=scope)
    plan_store = JvSpatialPlanStore(scope=scope)
    await plan_store.set_items(
        [PlanItem(id="plan-rotation", content="plan survives retirement")]
    )

    models_and_fields = (
        (HarnessRunRecord, {"run_key": scope.run_id}),
        (HarnessEventRecord, {"run_key": scope.run_id, "record_key": "event-1"}),
        (
            HarnessSnapshotRecord,
            {"run_key": scope.run_id, "record_key": "snapshot-1"},
        ),
        (
            HarnessCheckpointManifestRecord,
            {"run_key": scope.run_id, "checkpoint_key": "checkpoint-1"},
        ),
        (
            HarnessToolEffectRecord,
            {"run_key": scope.run_id, "tool_call_key": "tool-call-1"},
        ),
        (
            HarnessModelRequestRecord,
            {"run_key": scope.run_id, "request_key": "request-1"},
        ),
        (
            HarnessTurnInputRecord,
            {
                "principal_id": scope.principal_id,
                "workspace_id": scope.workspace_id,
                "thread_id": scope.thread_id,
                "work_item_id": "work-item-rotation",
                "accepted_message_id": "message-rotation",
                "client_request_id": "request-rotation",
            },
        ),
    )
    expected_payloads: dict[str, str] = {}
    for record_type, fields in models_and_fields:
        record_id = f"o.{record_type._entity_name()}.{uuid.uuid4().hex}"
        plaintext = f"rotation payload for {record_type.__name__}"
        stored, created = await record_type.create_if_absent(
            id=record_id,
            scope_key=step_store._scope_key,
            payload_ciphertext=encrypt_secret_for_storage(plaintext, aad=record_id),
            **fields,
        )
        assert created
        expected_payloads[stored.id] = plaintext

    keys.update(current=b"s" * 32, previous=b"r" * 32)
    report = await rewrap_harness_session_records(scope_key=step_store._scope_key)
    assert report["records_seen"] == 8
    assert report["records_rewrapped"] == 8
    for record_type, _ in models_and_fields:
        assert report[record_type.__name__] == 1
    assert report["HarnessPlanState"] == 1

    # Removing the old key is the decisive proof: every rewrapped record can
    # still be authenticated, not just the snapshot/manifest/plan subset.
    keys["previous"] = None
    for record_type, _ in models_and_fields:
        records = await record_type.find({"scope_key": step_store._scope_key})
        assert len(records) == 1
        record = records[0]
        assert (
            decrypt_secret_from_storage(record.payload_ciphertext, aad=record.id)
            == expected_payloads[record.id]
        )
        unchanged_ciphertext, changed = rewrap_secret_for_storage(
            record.payload_ciphertext, aad=record.id
        )
        assert not changed
        assert unchanged_ciphertext == record.payload_ciphertext
    assert await plan_store.get_items() == [
        PlanItem(id="plan-rotation", content="plan survives retirement")
    ]


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_postgres_checkpoint_rotation_preserves_concurrent_plan_update(
    postgres_graph_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plan write racing rewrap wins its revision/ciphertext compare-and-set."""
    import json

    from jvspatial.core.context import get_default_context

    from app.agentive.harness import key_rotation
    from app.agentive.harness.key_rotation import rewrap_harness_session_records
    from app.services.credential_crypto import encrypt_secret_for_storage

    _, scope = await _thread_and_scope()
    keys = {"current": b"t" * 32, "previous": None}
    monkeypatch.setattr(
        "app.services.credential_crypto._resolve_key",
        lambda: (keys["current"], None),
    )
    monkeypatch.setattr(
        "app.services.credential_crypto._previous_key", lambda: keys["previous"]
    )
    monkeypatch.setattr(key_rotation, "_current_key", lambda: keys["current"])
    monkeypatch.setattr(key_rotation, "_previous_key", lambda: keys["previous"])
    plan_store = JvSpatialPlanStore(scope=scope)
    await plan_store.set_items([PlanItem(id="plan-before-race", content="old plan")])
    keys.update(current=b"u" * 32, previous=b"t" * 32)

    database = get_default_context().database
    original_find_one_and_update = database.find_one_and_update
    raced = False

    async def concurrent_plan_write(collection, query, update):
        nonlocal raced
        if (
            collection == "object"
            and query.get("id") == plan_store._record_id
            and not raced
        ):
            raced = True
            current = await database.get("object", plan_store._record_id)
            assert current is not None
            context = current.get("context") or {}
            revision = int(context["revision"])
            concurrent_item = PlanItem(
                id="plan-concurrent", content="concurrent plan update survives"
            )
            encoded = json.dumps(
                [concurrent_item.model_dump(mode="json")],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            ciphertext = encrypt_secret_for_storage(encoded, aad=plan_store._record_id)
            concurrent = await original_find_one_and_update(
                "object",
                {
                    "id": plan_store._record_id,
                    "context.scope_key": plan_store._scope_key,
                    "context.revision": revision,
                },
                {
                    "$set": {
                        "context.revision": revision + 1,
                        "context.payload_ciphertext": ciphertext,
                    }
                },
            )
            assert concurrent is not None
        return await original_find_one_and_update(collection, query, update)

    monkeypatch.setattr(database, "find_one_and_update", concurrent_plan_write)
    report = await rewrap_harness_session_records(scope_key=plan_store._scope_key)

    assert raced
    assert report == {"records_seen": 1, "records_rewrapped": 0}
    assert await plan_store.get_items() == [
        PlanItem(id="plan-concurrent", content="concurrent plan update survives")
    ]


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_postgres_checkpoint_key_rotation_resumes_after_partial_failure(
    postgres_graph_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed row write leaves a sweep resumable without losing snapshots."""
    from app.agentive.harness import key_rotation
    from app.agentive.harness.key_rotation import rewrap_harness_session_records
    from app.models.harness_records import HarnessSnapshotRecord

    _, scope = await _thread_and_scope()
    keys = {"current": b"p" * 32, "previous": None}
    monkeypatch.setattr(
        "app.services.credential_crypto._resolve_key",
        lambda: (keys["current"], None),
    )
    monkeypatch.setattr(
        "app.services.credential_crypto._previous_key", lambda: keys["previous"]
    )
    monkeypatch.setattr(key_rotation, "_current_key", lambda: keys["current"])
    monkeypatch.setattr(key_rotation, "_previous_key", lambda: keys["previous"])
    store = JvSpatialStepStore(scope=scope)
    snapshots = [
        ContinuableSnapshot(
            run_id=scope.run_id,
            conversation_id=scope.session_id,
            step_index=index,
            messages=[ModelRequest(parts=[UserPromptPart(content=f"state-{index}")])],
            state="complete",
            idempotency_key=f"partial-rotation-{index}",
        )
        for index in (1, 2)
    ]
    for snapshot in snapshots:
        await store.save_snapshot(snapshot)

    keys.update(current=b"q" * 32, previous=b"p" * 32)
    original_save = HarnessSnapshotRecord.save
    save_attempts = 0

    async def fail_second_snapshot_save(record):
        nonlocal save_attempts
        save_attempts += 1
        if save_attempts == 2:
            raise RuntimeError("injected checkpoint rewrap interruption")
        await original_save(record)

    monkeypatch.setattr(HarnessSnapshotRecord, "save", fail_second_snapshot_save)
    with pytest.raises(RuntimeError, match="injected checkpoint rewrap interruption"):
        await rewrap_harness_session_records(scope_key=store._scope_key)
    assert save_attempts == 2

    monkeypatch.setattr(HarnessSnapshotRecord, "save", original_save)
    report = await rewrap_harness_session_records(scope_key=store._scope_key)
    assert report["records_seen"] == 2
    assert report["records_rewrapped"] == 1

    keys["previous"] = None
    assert await store.list_snapshots(run_id=scope.run_id) == snapshots


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_postgres_corrupt_snapshot_codec_fails_closed(
    postgres_graph_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A corrupt durable checkpoint is surfaced instead of resuming empty state."""
    _, scope = await _thread_and_scope()
    monkeypatch.setattr(
        "app.services.credential_crypto._resolve_key", lambda: (b"c" * 32, None)
    )
    store = JvSpatialStepStore(scope=scope)
    await store.save_snapshot(
        ContinuableSnapshot(
            run_id=scope.run_id,
            conversation_id=scope.session_id,
            step_index=1,
            messages=[ModelRequest(parts=[UserPromptPart(content="private state")])],
            state="complete",
            idempotency_key="corrupt-codec-snapshot",
        )
    )
    record = (await HarnessSnapshotRecord.find({"run_key": scope.run_id}))[0]
    record.payload_ciphertext = "v1:corrupt-codec"
    await record.save()

    with pytest.raises(HarnessPersistenceError, match="authenticated or decoded"):
        await store.latest_snapshot(run_id=scope.run_id)
