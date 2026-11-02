"""Persistence contract tests for the Core-owned Harness StepStore."""

from __future__ import annotations

import json
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
from app.agentive.harness.jvspatial_store import (
    HarnessPersistenceError,
    JvSpatialStepStore,
    _stable_id,
)
from app.agentive.harness.plan_store import JvSpatialPlanStore
from app.agentive.harness.pydantic_ai_compat import ModelMessagesTypeAdapter
from app.agentive.harness.scoped_store import ScopedStepStore
from app.models.harness_records import (
    HarnessEventRecord,
    HarnessMessageRecord,
    HarnessRunRecord,
    HarnessSnapshotRecord,
    HarnessToolEffectRecord,
)

RECORD_MODELS = (
    HarnessRunRecord,
    HarnessEventRecord,
    HarnessSnapshotRecord,
    HarnessMessageRecord,
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

            def matches(row: object) -> bool:
                for key, value in filters.items():
                    current = getattr(row, key)
                    if isinstance(value, dict) and "$in" in value:
                        if current not in value["$in"]:
                            return False
                    elif current != value:
                        return False
                return True

            return [row for row in rows[cls].values() if matches(row)]

        async def get(cls, obj_id):
            return rows[cls].get(obj_id)

        monkeypatch.setattr(model, "create_if_absent", classmethod(create_if_absent))
        monkeypatch.setattr(model, "find", classmethod(find))
        monkeypatch.setattr(model, "get", classmethod(get))

    from jvspatial.core.context import get_default_context

    database = get_default_context().database
    original_find = database.find

    async def database_find(collection, query, *, limit=None, sort=None):
        if (
            isinstance(query, dict)
            and query.get("entity") == "HarnessSnapshotRecord"
            and "context.scope_key" in query
        ):
            matched = [
                row
                for row in rows[HarnessSnapshotRecord].values()
                if row.scope_key == query["context.scope_key"]
                and row.run_key == query.get("context.run_key")
                and (
                    "context.state" not in query or row.state == query["context.state"]
                )
            ]
            if sort:
                for field, direction in reversed(sort):
                    name = field.rsplit(".", 1)[-1]

                    def sort_value(item, name=name):
                        return getattr(item, name)

                    matched.sort(key=sort_value, reverse=direction < 0)
            if limit is not None:
                matched = matched[:limit]
            return [
                {
                    "id": row.id,
                    "entity": "HarnessSnapshotRecord",
                    "context": {
                        "scope_key": row.scope_key,
                        "run_key": row.run_key,
                        "record_key": row.record_key,
                        "occurred_at": row.occurred_at,
                        "step_index": row.step_index,
                        "state": row.state,
                        "payload_ciphertext": row.payload_ciphertext,
                    },
                }
                for row in matched
            ]
        return await original_find(collection, query, limit=limit, sort=sort)

    monkeypatch.setattr(database, "find", database_find)
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


@pytest.mark.asyncio
async def test_checkpoint_messages_are_stored_once_per_scope(
    persisted_objects: dict[type, dict[str, object]],
) -> None:
    """Repeated history adds a ref, not another copy of the message body."""
    scope = _scope("workspace-a", "user-a")
    other = _scope("workspace-b", "user-b")
    store = JvSpatialStepStore(scope=scope)
    other_store = JvSpatialStepStore(scope=other)
    now = datetime.now(timezone.utc)
    body = "shared-history-" + ("x" * 4000)
    shared = ModelRequest(parts=[UserPromptPart(content=body)])
    added = ModelRequest(parts=[UserPromptPart(content="second step")])

    await store.save_snapshot(
        ContinuableSnapshot(
            run_id="run-1",
            conversation_id="session-1",
            step_index=1,
            timestamp=now,
            messages=[shared],
            idempotency_key="snapshot-1",
        )
    )
    await store.save_snapshot(
        ContinuableSnapshot(
            run_id="run-1",
            conversation_id="session-1",
            step_index=2,
            timestamp=now + timedelta(seconds=1),
            messages=[shared, added],
            idempotency_key="snapshot-2",
        )
    )
    await other_store.save_snapshot(
        ContinuableSnapshot(
            run_id="run-1",
            conversation_id="session-1",
            step_index=1,
            timestamp=now,
            messages=[shared],
            idempotency_key="snapshot-1",
        )
    )

    messages = persisted_objects[HarnessMessageRecord]
    snapshots = persisted_objects[HarnessSnapshotRecord]
    assert len(messages) == 3
    assert (
        len([row for row in messages.values() if row.scope_key == store._scope_key])
        == 2
    )
    assert all(body not in row.payload_ciphertext for row in snapshots.values())
    assert all(len(row.payload_ciphertext) < len(body) for row in snapshots.values())
    restored = await store.list_snapshots(run_id="run-1")
    assert [item.step_index for item in restored] == [1, 2]
    assert restored[1].messages == [shared, added]
    assert await other_store.latest_snapshot(run_id="run-1")
    assert (await other_store.latest_snapshot(run_id="run-1")).messages == [shared]


@pytest.mark.asyncio
async def test_legacy_inline_checkpoint_still_reads_and_replays(
    persisted_objects: dict[type, dict[str, object]],
) -> None:
    """Checkpoints written with inline history remain authoritative."""
    scope = _scope("workspace-a", "user-a")
    store = JvSpatialStepStore(scope=scope)
    now = datetime.now(timezone.utc)
    message = ModelRequest(parts=[UserPromptPart(content="legacy transcript")])
    snapshot = ContinuableSnapshot(
        run_id="run-1",
        conversation_id="session-1",
        step_index=1,
        timestamp=now,
        messages=[message],
        idempotency_key="legacy-snapshot",
        state="complete",
    )
    record_id = _stable_id(
        HarnessSnapshotRecord,
        store._record_key("snapshot", f"{snapshot.run_id}:{snapshot.idempotency_key}"),
    )
    payload = {
        "run_id": snapshot.run_id,
        "step_index": snapshot.step_index,
        "messages": json.loads(ModelMessagesTypeAdapter.dump_json(snapshot.messages)),
        "conversation_id": snapshot.conversation_id,
        "parent_run_id": snapshot.parent_run_id,
        "agent_name": snapshot.agent_name,
        "timestamp": now.isoformat(),
        "state": snapshot.state,
        "idempotency_key": snapshot.idempotency_key,
    }
    row = HarnessSnapshotRecord(
        id=record_id,
        scope_key=store._scope_key,
        run_key=snapshot.run_id,
        record_key=snapshot.idempotency_key,
        occurred_at=now.isoformat(),
        step_index=snapshot.step_index,
        state=snapshot.state,
        payload_ciphertext=store._encrypt(payload, record_id=record_id),
    )
    persisted_objects[HarnessSnapshotRecord][record_id] = row
    assert await store.latest_snapshot(run_id="run-1") == snapshot

    await store.save_snapshot(snapshot)
    assert len(persisted_objects[HarnessSnapshotRecord]) == 1
    assert persisted_objects[HarnessSnapshotRecord][record_id].payload_ciphertext == (
        row.payload_ciphertext
    )
    assert await store.latest_snapshot(run_id="run-1") == snapshot


@pytest.mark.asyncio
async def test_missing_checkpoint_message_fails_closed(
    persisted_objects: dict[type, dict[str, object]],
) -> None:
    """A ref without its scoped message is not an empty history."""
    scope = _scope("workspace-a", "user-a")
    store = JvSpatialStepStore(scope=scope)
    now = datetime.now(timezone.utc)
    await store.save_snapshot(
        ContinuableSnapshot(
            run_id="run-1",
            conversation_id="session-1",
            step_index=1,
            timestamp=now,
            messages=[ModelRequest(parts=[UserPromptPart(content="kept")])],
            idempotency_key="snapshot-1",
        )
    )
    persisted_objects[HarnessMessageRecord].clear()
    with pytest.raises(HarnessPersistenceError, match="scope does not match"):
        await store.latest_snapshot(run_id="run-1")


@pytest.mark.asyncio
async def test_checkpoint_message_io_is_batched_per_snapshot(
    persisted_objects: dict[type, dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A history lookup is one id-set read, and unchanged messages are not rewritten."""
    import asyncio

    finds: list[dict] = []
    creates: list[str] = []
    current_find = HarnessMessageRecord.find
    current_create = HarnessMessageRecord.create_if_absent

    async def counting_find(cls, query=None, **kwargs):
        finds.append(dict(query or {}))
        return await current_find.__func__(cls, query, **kwargs)

    async def counting_create(cls, **kwargs):
        creates.append(kwargs["id"])
        return await current_create.__func__(cls, **kwargs)

    monkeypatch.setattr(HarnessMessageRecord, "find", classmethod(counting_find))
    monkeypatch.setattr(
        HarnessMessageRecord, "create_if_absent", classmethod(counting_create)
    )
    scope = _scope("workspace-a", "user-a")
    store = JvSpatialStepStore(scope=scope)
    now = datetime.now(timezone.utc)
    first = [
        ModelRequest(parts=[UserPromptPart(content=f"history-{index}")])
        for index in range(3)
    ]
    added = ModelRequest(parts=[UserPromptPart(content="history-new")])
    await store.save_snapshot(
        ContinuableSnapshot(
            run_id="run-1",
            conversation_id="session-1",
            step_index=1,
            timestamp=now,
            messages=first,
            idempotency_key="snapshot-1",
        )
    )
    await store.save_snapshot(
        ContinuableSnapshot(
            run_id="run-1",
            conversation_id="session-1",
            step_index=2,
            timestamp=now + timedelta(seconds=1),
            messages=[*first, added],
            idempotency_key="snapshot-2",
        )
    )
    assert len(finds) == 2
    assert all("$in" in item["id"] for item in finds)
    assert len(creates) == 4
    finds.clear()
    restored = await store.list_snapshots(run_id="run-1")
    assert len(finds) == 1
    assert len(finds[0]["id"]["$in"]) == 4
    assert restored[1].messages == [*first, added]

    other = JvSpatialStepStore(scope=_scope("workspace-b", "user-b"))
    shared = ModelRequest(parts=[UserPromptPart(content="same words")])
    await asyncio.gather(
        store.save_snapshot(
            ContinuableSnapshot(
                run_id="run-1",
                conversation_id="session-1",
                step_index=3,
                timestamp=now + timedelta(seconds=2),
                messages=[shared],
                idempotency_key="snapshot-3",
            )
        ),
        other.save_snapshot(
            ContinuableSnapshot(
                run_id="run-1",
                conversation_id="session-1",
                step_index=1,
                timestamp=now,
                messages=[shared],
                idempotency_key="snapshot-1",
            )
        ),
    )
    assert len(persisted_objects[HarnessMessageRecord]) == 6
    assert (await other.latest_snapshot(run_id="run-1")).messages == [shared]


@pytest.mark.asyncio
async def test_missing_message_inserts_stay_bounded(
    persisted_objects: dict[type, dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A long new history does not open one connection per message."""
    import asyncio

    from app.agentive.harness.jvspatial_store import _MESSAGE_INSERT_CONCURRENCY

    current = HarnessMessageRecord.create_if_absent
    inflight = 0
    peak = 0

    async def tracked_create(cls, **kwargs):
        nonlocal inflight, peak
        inflight += 1
        peak = max(peak, inflight)
        await asyncio.sleep(0)
        try:
            return await current.__func__(cls, **kwargs)
        finally:
            inflight -= 1

    monkeypatch.setattr(
        HarnessMessageRecord, "create_if_absent", classmethod(tracked_create)
    )
    store = JvSpatialStepStore(scope=_scope("workspace-a", "user-a"))
    now = datetime.now(timezone.utc)
    messages = [
        ModelRequest(parts=[UserPromptPart(content=f"bulk-{index}")])
        for index in range(_MESSAGE_INSERT_CONCURRENCY + 12)
    ]
    await store.save_snapshot(
        ContinuableSnapshot(
            run_id="run-1",
            conversation_id="session-1",
            step_index=1,
            timestamp=now,
            messages=messages,
            idempotency_key="snapshot-bulk",
        )
    )
    assert 1 < peak <= _MESSAGE_INSERT_CONCURRENCY
    assert len(persisted_objects[HarnessMessageRecord]) == len(messages)


@pytest.mark.asyncio
async def test_latest_snapshot_is_one_ordered_database_read(
    persisted_objects: dict[type, dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Latest checkpoint selection is filtered, ordered and limited in the query."""
    from jvspatial.core.context import get_default_context

    context = get_default_context()
    current = context.database.find
    seen: list[dict] = []

    async def recording_find(collection, query, *, limit=None, sort=None):
        if isinstance(query, dict) and query.get("entity") == "HarnessSnapshotRecord":
            seen.append({"limit": limit, "sort": sort, "query": dict(query)})
        return await current(collection, query, limit=limit, sort=sort)

    monkeypatch.setattr(context.database, "find", recording_find)
    store = JvSpatialStepStore(scope=_scope("workspace-a", "user-a"))
    now = datetime.now(timezone.utc)
    for step, state in ((1, "complete"), (4, "interrupted"), (2, "complete")):
        await store.save_snapshot(
            ContinuableSnapshot(
                run_id="run-1",
                conversation_id="session-1",
                step_index=step,
                timestamp=now + timedelta(seconds=step),
                messages=[ModelRequest(parts=[UserPromptPart(content=f"step-{step}")])],
                state=state,
                idempotency_key=f"snapshot-{step}",
            )
        )
    seen.clear()
    latest = await store.latest_snapshot(run_id="run-1")
    assert latest is not None and latest.step_index == 2
    assert seen[0]["limit"] == 1
    assert seen[0]["sort"] == [
        ("context.step_index", -1),
        ("context.occurred_at", -1),
    ]
    assert seen[0]["query"]["context.state"] == "complete"
    including = await store.latest_snapshot(run_id="run-1", include_interrupted=True)
    assert including is not None and including.step_index == 4
    assert "context.state" not in seen[-1]["query"]
