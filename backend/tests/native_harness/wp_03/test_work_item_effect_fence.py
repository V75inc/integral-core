"""PostgreSQL atomicity checks for future durable native-harness effects."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone, tzinfo
from decimal import Decimal

import pytest
from jvspatial.core.context import GraphContext, set_default_context
from pydantic import ValidationError
from pydantic_ai_harness.step_persistence import StepEvent

from app.agentive.harness.contracts import HarnessExecutionScope
from app.agentive.harness.jvspatial_store import JvSpatialStepStore
from app.agentive.harness.scoped_store import ScopedStepStore
from app.agentive.services import work_items
from app.agentive.services.work_execution import build_work_execution_context
from app.agentive.work_models import WorkItem
from app.models.harness_records import (
    HarnessEventRecord,
    HarnessModelRequestRecord,
    HarnessTurnInputRecord,
)
from app.models.nodes import ChatThread, HarnessSession
from app.schemas.agentive.work import WorkError, WorkExecutionContext


@pytest.fixture
def postgres_graph_context(postgres_raw_db):
    """Bind Object writes and WorkItem CAS to the isolated PostgreSQL store."""
    from jvspatial.core.context import _default_context_var

    token = set_default_context(GraphContext(database=postgres_raw_db))
    try:
        yield
    finally:
        _default_context_var.reset(token)


async def _claimed_context(
    *, deadline_at: str | None = None
) -> tuple[WorkItem, WorkExecutionContext]:
    suffix = uuid.uuid4().hex
    queued = await work_items.enqueue_work_item(
        kind="capability",
        origin="test",
        principal_id=f"fence-user-{suffix}",
        workspace_id=f"fence-workspace-{suffix}",
        thread_id=f"fence-thread-{suffix}",
        idempotency_key=f"fence-{suffix}",
        input_payload={"capability_key": "test.noop"},
        deadline_at=deadline_at,
    )
    claimed = await work_items.claim_due_candidate(
        worker_id=f"fence-worker-{suffix}",
        lease_seconds=60,
        work_item_id=queued.work_item_id,
    )
    assert claimed is not None
    context = build_work_execution_context(
        work_item=claimed,
        logical_step_key="native-harness:0",
    )
    return claimed, context


def test_work_execution_authority_is_frozen_and_strict() -> None:
    """Execution authority rejects mutation and noncanonical field types."""
    context = WorkExecutionContext(
        work_item_id="work-1",
        attempt=1,
        run_id="run-1",
        principal_id="user-1",
        workspace_id="workspace-1",
        thread_id="thread-1",
        logical_step_key="native-harness:0",
        effect_key="effect-1",
        lease_token="opaque-token",
        lease_fence=1,
    )
    with pytest.raises(ValidationError):
        context.lease_fence = 2  # type: ignore[misc]
    with pytest.raises(ValidationError):
        WorkExecutionContext.model_validate(
            {
                **context.model_dump(),
                "attempt": "1",
            }
        )


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed_field,changed_value",
    [
        ("thread_id", "another-thread"),
        ("run_id", "forged-run"),
        ("effect_key", "forged-effect"),
        ("deadline_at", "2099-01-01T00:00:00+00:00"),
    ],
)
async def test_work_execution_context_must_match_claimed_row_contract(
    postgres_graph_context,
    changed_field: str,
    changed_value: str,
) -> None:
    """Reject changed thread/run/effect/deadline authority before side effects."""
    item, context = await _claimed_context()
    forged = context.model_copy(update={changed_field: changed_value})

    with pytest.raises(WorkError) as exc_info:
        async with work_items.authorized_work_item_effect(forged):
            pytest.fail("forged execution authority must not enter the effect")

    assert exc_info.value.code == "work.lease_lost"
    assert context.lease_token not in str(exc_info.value)
    unchanged = await WorkItem.get(item.id)
    assert unchanged is not None
    assert unchanged.status == "running"
    assert unchanged.lease_fence == context.lease_fence


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_harness_session_activation_and_run_claim_share_workitem_fence(
    postgres_graph_context,
) -> None:
    """Persist session activation and run claim under the same WorkItem fence."""
    from app.agentive.harness.contracts import HarnessExecutionScope
    from app.services.harness_sessions import (
        claim_harness_run,
        ensure_harness_session,
    )

    item, context = await _claimed_context()
    thread = await ChatThread.create(
        id=context.thread_id,
        user_id=context.principal_id,
        workspace_id=context.workspace_id,
        provider_id="integral_native",
        created_at="2026-10-04T12:00:00+00:00",
        updated_at="2026-10-04T12:00:00+00:00",
        last_message_at="2026-10-04T12:00:00+00:00",
    )
    scope = HarnessExecutionScope(
        tenant_id=context.workspace_id,
        principal_id=context.principal_id,
        workspace_id=context.workspace_id,
        thread_id=context.thread_id,
        session_id=f"session-{uuid.uuid4().hex}",
        run_id=context.run_id,
        permission_revision="permission-r1",
        capability_version="capability-r1",
    )

    session = await ensure_harness_session(
        scope=scope,
        binding_id="pydantic_ai_v1",
        work_execution_context=context,
    )
    claimed = await claim_harness_run(
        scope=scope,
        work_execution_context=context,
    )
    assert claimed.id == session.id
    assert claimed.last_run_id == context.run_id

    await work_items.force_expire_lease_for_tests(item.work_item_id)
    await work_items.reclaim_expired_lease(
        item.work_item_id,
        worker_id="replacement-worker",
        lease_seconds=60,
    )
    with pytest.raises(WorkError) as exc_info:
        await claim_harness_run(
            scope=scope,
            work_execution_context=context,
        )
    assert exc_info.value.code == "work.lease_lost"

    persisted = await HarnessSession.get(session.id)
    assert persisted is not None
    assert persisted.last_run_id == context.run_id
    updated_thread = await ChatThread.get(thread.id)
    assert updated_thread is not None
    assert updated_thread.active_harness_session_id == session.id


def _turn_input_record(record_id: str, item: WorkItem) -> dict[str, object]:
    return {
        "id": record_id,
        "scope_key": f"scope-{item.work_item_id}",
        "principal_id": item.principal_id,
        "workspace_id": item.workspace_id,
        "thread_id": item.thread_id,
        "work_item_id": item.work_item_id,
        "accepted_message_id": f"message-{item.work_item_id}",
        "client_request_id": f"request-{item.work_item_id}",
        "created_at": "2026-10-04T12:00:00+00:00",
        "expires_at": "2026-10-05T12:00:00+00:00",
        "schema_version": 1,
        "payload_digest": "sha256:test",
        "payload_ciphertext": "ciphertext",
    }


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_fenced_effect_write_commits_with_authority_row_lock(
    postgres_graph_context,
) -> None:
    """Commit an effect while holding the current WorkItem row fence."""
    item, context = await _claimed_context()
    record_id = f"o.HarnessTurnInputRecord.{uuid.uuid4().hex}"

    async with work_items.authorized_work_item_effect(context):
        record, created = await HarnessTurnInputRecord.create_if_absent(
            **_turn_input_record(record_id, item)
        )

    assert created is True
    assert await HarnessTurnInputRecord.get(record.id) is not None


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_fenced_effect_and_workitem_authority_roll_back_together(
    postgres_graph_context,
) -> None:
    """Roll back effect data when its fenced transaction does not complete."""
    item, context = await _claimed_context()
    record_id = f"o.HarnessTurnInputRecord.{uuid.uuid4().hex}"

    with pytest.raises(RuntimeError, match="effect failed"):
        async with work_items.authorized_work_item_effect(context):
            await HarnessTurnInputRecord.create_if_absent(
                **_turn_input_record(record_id, item)
            )
            raise RuntimeError("effect failed")

    assert await HarnessTurnInputRecord.get(record_id) is None
    still_running = await WorkItem.get(item.id)
    assert still_running is not None
    assert still_running.status == "running"
    assert still_running.lease_fence == context.lease_fence


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_stale_fence_cannot_enter_effect_transaction(
    postgres_graph_context,
) -> None:
    """Reject an expired and reclaimed attempt before it enters an effect."""
    item, context = await _claimed_context()
    await work_items.force_expire_lease_for_tests(item.work_item_id)
    reclaimed = await work_items.reclaim_expired_lease(
        item.work_item_id,
        worker_id="replacement-worker",
        lease_seconds=60,
    )
    assert reclaimed.lease_fence == context.lease_fence + 1

    with pytest.raises(WorkError) as exc_info:
        async with work_items.authorized_work_item_effect(context):
            pytest.fail("a stale lease must not reach the effect body")

    assert exc_info.value.code == "work.lease_lost"
    assert context.lease_token not in str(exc_info.value)


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_durable_cancel_blocks_effect_transaction(postgres_graph_context) -> None:
    """Durable cancellation prevents further effects under the old claim."""
    item, context = await _claimed_context()
    await work_items.cancel_work_item(item.work_item_id)

    with pytest.raises(WorkError) as exc_info:
        async with work_items.authorized_work_item_effect(context):
            pytest.fail("durable cancellation must block effect execution")

    assert exc_info.value.code == "work.cancelled"
    assert context.lease_token not in str(exc_info.value)


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_fake_clock_controls_lease_renewal_expiry_and_deadline(
    postgres_graph_context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use a frozen clock to prove lease and deadline boundary decisions."""
    deadline_at = (datetime.now(timezone.utc) + timedelta(seconds=70)).isoformat()
    item, context = await _claimed_context(deadline_at=deadline_at)

    class FrozenDateTime(datetime):
        current = datetime.fromisoformat(item.lease_expires_at)

        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:
            if tz is None:
                return cls.current.replace(tzinfo=None)
            return cls.current.astimezone(tz)

    monkeypatch.setattr(work_items, "datetime", FrozenDateTime)
    FrozenDateTime.current -= timedelta(seconds=30)
    renewed = await work_items.heartbeat_lease(
        item.work_item_id,
        lease_token=context.lease_token,
        lease_fence=context.lease_fence,
        worker_id=item.lease_owner,
        lease_seconds=60,
    )
    renewed_expiry = datetime.fromisoformat(renewed.lease_expires_at)
    assert renewed_expiry == FrozenDateTime.current + timedelta(seconds=60)

    FrozenDateTime.current = renewed_expiry
    with pytest.raises(WorkError) as lease_error:
        async with work_items.authorized_work_item_effect(context):
            pytest.fail("an expired lease must not enter the effect body")
    assert lease_error.value.code == "work.lease_lost"
    assert context.lease_token not in str(lease_error.value)

    FrozenDateTime.current = datetime.fromisoformat(
        context.deadline_at or ""
    ) + timedelta(seconds=1)
    with pytest.raises(WorkError) as deadline_error:
        async with work_items.authorized_work_item_effect(context):
            pytest.fail("an elapsed deadline must not enter the effect body")
    assert deadline_error.value.code == "work.deadline_exceeded"
    assert context.lease_token not in str(deadline_error.value)


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_native_step_store_event_uses_atomic_workitem_fence(
    postgres_graph_context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fence event sequence writes at the transaction-backed StepStore."""
    item, context = await _claimed_context()
    monkeypatch.setattr(
        "app.services.credential_crypto._resolve_key", lambda: (b"k" * 32, None)
    )
    scope = HarnessExecutionScope(
        tenant_id=item.workspace_id,
        principal_id=item.principal_id,
        workspace_id=item.workspace_id,
        thread_id=item.thread_id,
        session_id=f"session-{item.work_item_id}",
        run_id=context.run_id,
        permission_revision="permissions-1",
        capability_version="tools-1",
    )
    backend = JvSpatialStepStore(scope=scope)
    store = ScopedStepStore(
        store=backend,
        scope=scope,
        work_execution_context=context,
    )
    now = datetime.now(timezone.utc)

    await store.append_event(
        StepEvent(
            run_id=context.run_id,
            conversation_id=scope.session_id,
            kind="run_started",
            step_index=0,
            timestamp=now,
            idempotency_key="fenced-event-0",
        )
    )

    stored = await HarnessEventRecord.find(
        {
            "scope_key": backend._scope_key,
            "record_key": store._map_required("fenced-event-0"),
        }
    )
    assert len(stored) == 1

    await work_items.force_expire_lease_for_tests(item.work_item_id)
    await work_items.reclaim_expired_lease(
        item.work_item_id,
        worker_id="replacement-worker",
        lease_seconds=60,
    )
    with pytest.raises(WorkError) as exc_info:
        await store.append_event(
            StepEvent(
                run_id=context.run_id,
                conversation_id=scope.session_id,
                kind="late_event",
                step_index=1,
                timestamp=now,
                idempotency_key="fenced-event-late",
            )
        )

    assert exc_info.value.code == "work.lease_lost"
    assert "token" not in str(exc_info.value)
    late = await HarnessEventRecord.find(
        {
            "scope_key": backend._scope_key,
            "record_key": store._map_required("fenced-event-late"),
        }
    )
    assert late == []


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_stale_workitem_fence_rejects_model_usage_completion(
    postgres_graph_context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reclaimed attempt cannot append a terminal model-usage receipt."""
    from app.agentive.harness.contracts import (
        ModelUsageObservation,
        PhysicalModelRequest,
    )
    from app.agentive.harness.model_observations import (
        _scope_key,
        list_model_request_observations,
        persist_model_request_observation,
    )

    item, context = await _claimed_context()
    monkeypatch.setattr(
        "app.services.credential_crypto._resolve_key", lambda: (b"k" * 32, None)
    )
    scope = HarnessExecutionScope(
        tenant_id=item.workspace_id,
        principal_id=item.principal_id,
        workspace_id=item.workspace_id,
        thread_id=item.thread_id,
        session_id=f"session-{item.work_item_id}",
        run_id=context.run_id,
        permission_revision="permissions-1",
        capability_version="tools-1",
    )
    now = datetime.now(timezone.utc)
    dispatch = PhysicalModelRequest(
        request_id="physical-request-1",
        scope=scope,
        provider="ollama",
        model="gemma4:26b",
        attempt=1,
        dispatched_at=now,
        observed_at=now,
        outcome="dispatch_intent",
    )
    await persist_model_request_observation(dispatch, work_execution_context=context)

    await work_items.force_expire_lease_for_tests(item.work_item_id)
    await work_items.reclaim_expired_lease(
        item.work_item_id,
        worker_id="replacement-worker",
        lease_seconds=60,
    )
    response = dispatch.model_copy(
        update={
            "outcome": "responded",
            "observed_at": datetime.now(timezone.utc),
            "usage": ModelUsageObservation(
                input_tokens=12,
                output_tokens=5,
                provider_cost_usd=Decimal("0.01"),
                cost_source="provider_response",
                complete=True,
            ),
        }
    )
    with pytest.raises(WorkError) as exc_info:
        await persist_model_request_observation(
            response, work_execution_context=context
        )

    assert exc_info.value.code == "work.lease_lost"
    records = await HarnessModelRequestRecord.find(
        {"scope_key": _scope_key(scope), "run_key": scope.run_id}
    )
    assert len(records) == 1
    observations = await list_model_request_observations(scope=scope)
    assert [observation.outcome for observation in observations] == ["dispatch_intent"]


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_stale_workitem_fence_rejects_checkpoint_manifest_write(
    postgres_graph_context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reclaimed attempt cannot persist a resume-authorizing manifest."""
    from app.agentive.harness.checkpoint_manifests import (
        manifest_record_id,
        persist_checkpoint_manifest,
    )
    from app.agentive.harness.contracts import HarnessCheckpointManifest
    from app.models.harness_records import HarnessCheckpointManifestRecord

    item, context = await _claimed_context()
    monkeypatch.setattr(
        "app.services.credential_crypto._resolve_key", lambda: (b"k" * 32, None)
    )
    scope = HarnessExecutionScope(
        tenant_id=item.workspace_id,
        principal_id=item.principal_id,
        workspace_id=item.workspace_id,
        thread_id=item.thread_id,
        session_id=f"session-{item.work_item_id}",
        run_id=context.run_id,
        permission_revision="permissions-1",
        capability_version="tools-1",
    )
    manifest = HarnessCheckpointManifest(
        scope=scope,
        execution_fence_id=scope.run_id,
        framework_snapshot_id="snapshot-after-lease-loss",
        snapshot_step_index=3,
        snapshot_state="complete",
        safe_for_resume=True,
        framework_codec_version="messages-v1",
        capability_fingerprint=scope.capability_version,
        capability_restore_policies={"conversation_messages": "restore"},
        plan_revision="plan-r1",
    )

    await work_items.force_expire_lease_for_tests(item.work_item_id)
    await work_items.reclaim_expired_lease(
        item.work_item_id,
        worker_id="replacement-worker",
        lease_seconds=60,
    )
    with pytest.raises(WorkError) as exc_info:
        await persist_checkpoint_manifest(manifest, work_execution_context=context)

    assert exc_info.value.code == "work.lease_lost"
    record = await HarnessCheckpointManifestRecord.get(
        manifest_record_id(scope=scope, snapshot_id=manifest.framework_snapshot_id)
    )
    assert record is None


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_stale_workitem_fence_blocks_brokered_capability_and_receipt(
    postgres_graph_context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale WorkItem cannot enter Core broker dispatch or write a receipt."""
    from types import SimpleNamespace

    from app.agentive.harness.broker_tools import build_brokered_tools
    from app.models.harness_records import HarnessToolEffectRecord
    from app.schemas.capability_broker import CapabilityResult

    item, context = await _claimed_context()
    scope = HarnessExecutionScope(
        tenant_id=item.workspace_id,
        principal_id=item.principal_id,
        workspace_id=item.workspace_id,
        thread_id=item.thread_id,
        session_id=f"session-{item.work_item_id}",
        run_id=context.run_id,
        permission_revision="permissions-1",
        capability_version="tools-1",
    )
    invocations = []

    async def dispatch(**kwargs):
        invocations.append(kwargs)
        return CapabilityResult(ok=True, data={"effect": "committed"})

    monkeypatch.setattr(
        "app.agentive.services.capability_broker.infer_source_and_op_class",
        lambda _name: ("core", "write"),
    )
    monkeypatch.setattr(
        "app.agentive.services.capability_broker.invoke_declared_capability",
        dispatch,
    )
    tools = build_brokered_tools(
        scope=scope,
        catalogue=[
            {
                "name": "test_commit_effect",
                "description": "Commit one test effect.",
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
        work_execution_context=context,
    )

    await work_items.force_expire_lease_for_tests(item.work_item_id)
    await work_items.reclaim_expired_lease(
        item.work_item_id,
        worker_id="replacement-worker",
        lease_seconds=60,
    )
    with pytest.raises(WorkError) as exc_info:
        await tools[0].function_schema.call(
            {}, SimpleNamespace(tool_call_id="late-call")
        )

    assert exc_info.value.code == "work.lease_lost"
    assert invocations == []
    records = await HarnessToolEffectRecord.find({"run_key": scope.run_id})
    assert records == []
