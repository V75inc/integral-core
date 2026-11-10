"""Native deferred frontier persists in Integral's encrypted scoped StepStore."""

import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async
from pydantic_ai import Agent, CallDeferred
from pydantic_ai.messages import DeferredToolRequestsEvent, ModelRequest, UserPromptPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.tools import DeferredToolRequests
from pydantic_ai_harness.step_persistence import (
    ContinuableSnapshot,
    StepPersistence,
    ToolEffectRecord,
)

from app.agentive.harness.contracts import HarnessExecutionScope
from app.agentive.harness.jvspatial_store import (
    HarnessPersistenceError,
    JvSpatialStepStore,
)
from app.agentive.harness.scoped_store import ScopedStepStore

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


async def test_concurrent_initial_tool_call_has_one_dispatch_winner(
    postgres_raw_db, monkeypatch
):
    """An empty-ledger race cannot admit two starts for the same native call."""
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "22" * 32)
    workspace = "workspace-" + uuid4().hex
    scope = HarnessExecutionScope(
        tenant_id=workspace,
        principal_id="principal-" + uuid4().hex,
        workspace_id=workspace,
        thread_id="thread-" + uuid4().hex,
        session_id="conversation-" + uuid4().hex,
        run_id="effect-" + uuid4().hex,
        permission_revision="permissions-1",
        capability_version="capabilities-1",
    )
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        backing = JvSpatialStepStore(scope=scope)
        store = ScopedStepStore(store=backing, scope=scope)
        original_get = backing.get_tool_effect
        both_read = asyncio.Event()
        reads = 0

        async def synchronized_get(**kwargs):
            nonlocal reads
            result = await original_get(**kwargs)
            reads += 1
            if reads == 2:
                both_read.set()
            await asyncio.wait_for(both_read.wait(), timeout=10)
            return result

        calls = []

        async def start_and_dispatch(started):
            await store.record_tool_effect(
                ToolEffectRecord(
                    run_id=scope.run_id,
                    tool_call_id="same-call",
                    tool_name="write",
                    status="started",
                    started_at=started,
                )
            )
            calls.append(started)

        now = datetime.now(timezone.utc)
        with monkeypatch.context() as patch:
            patch.setattr(backing, "get_tool_effect", synchronized_get)
            outcomes = await asyncio.gather(
                start_and_dispatch(now),
                start_and_dispatch(now + timedelta(microseconds=1)),
                return_exceptions=True,
            )
        assert len(calls) == 1
        assert (
            sum(isinstance(outcome, HarnessPersistenceError) for outcome in outcomes)
            == 1
        )
        unresolved = await store.list_unresolved_tool_effects(run_id=scope.run_id)
        assert len(unresolved) == 1
        assert unresolved[0].started_at == calls[0]


@pytest.mark.parametrize("conflict", [False, True])
async def test_tool_effect_outcome_remains_safe_across_clock_reversal(
    postgres_raw_db, monkeypatch, conflict
):
    """Postgres recovery folds exact call evidence rather than timestamp order."""
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "22" * 32)
    workspace = "workspace-" + uuid4().hex
    scope = HarnessExecutionScope(
        tenant_id=workspace,
        principal_id="principal-" + uuid4().hex,
        workspace_id=workspace,
        thread_id="thread-" + uuid4().hex,
        session_id="conversation-" + uuid4().hex,
        run_id="effect-" + uuid4().hex,
        permission_revision="permissions-1",
        capability_version="capabilities-1",
    )
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        store = ScopedStepStore(store=JvSpatialStepStore(scope=scope), scope=scope)
        started = datetime.now(timezone.utc)
        base = {
            "run_id": scope.run_id,
            "tool_call_id": "call-1",
            "tool_name": "write",
            "started_at": started,
        }
        await store.record_tool_effect(ToolEffectRecord(**base, status="started"))
        await store.record_tool_effect(
            ToolEffectRecord(
                **base, status="completed", ended_at=started - timedelta(seconds=1)
            )
        )
        if conflict:
            conflicting = ToolEffectRecord(**base, status="failed", ended_at=started)
            with pytest.raises(
                HarnessPersistenceError, match="transition is ambiguous"
            ):
                await store.record_tool_effect(conflicting)

            async def no_prior(**_kwargs):
                return None

            # Simulate an older store that had already persisted conflicting facts.
            with monkeypatch.context() as patch:
                patch.setattr(store._store, "get_tool_effect", no_prior)
                await store.record_tool_effect(conflicting)
            with pytest.raises(HarnessPersistenceError, match="conflict"):
                await store.list_unresolved_tool_effects(run_id=scope.run_id)
        else:
            assert await store.list_unresolved_tool_effects(run_id=scope.run_id) == []
            effect = await store.get_tool_effect(
                run_id=scope.run_id, tool_call_id="call-1"
            )
            assert effect is not None and effect.status == "completed"


@pytest.mark.parametrize("legacy", ["current", "sentinel", "missing", "mixed"])
async def test_native_sequence_orders_postgres_snapshots_when_clock_reverses(
    postgres_raw_db, monkeypatch, legacy
):
    """Native ordinals win over wall-clock and reset graph-step values."""
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "22" * 32)
    workspace = "workspace-" + uuid4().hex
    scope = HarnessExecutionScope(
        tenant_id=workspace,
        principal_id="principal-" + uuid4().hex,
        workspace_id=workspace,
        thread_id="thread-" + uuid4().hex,
        session_id="conversation-" + uuid4().hex,
        run_id="order-" + uuid4().hex,
        permission_revision="permissions-1",
        capability_version="capabilities-1",
    )
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        store = ScopedStepStore(store=JvSpatialStepStore(scope=scope), scope=scope)
        now = datetime.now(timezone.utc)
        for index in range(12):
            step, state = (0, "interrupted") if index == 11 else (4, "complete")
            await store.save_snapshot(
                ContinuableSnapshot(
                    run_id=scope.run_id,
                    conversation_id=scope.session_id,
                    step_index=step,
                    timestamp=now - timedelta(seconds=index),
                    messages=[
                        ModelRequest(parts=[UserPromptPart(content=f"saved-{index}")])
                    ],
                    state=state,
                    idempotency_key=f"{index}:{step}:{state}",
                )
            )
        if legacy != "current":
            rows = await postgres_raw_db.find(
                "object", {"entity": "HarnessSnapshotRecord"}
            )
            for index, row in enumerate(rows):
                if legacy == "mixed" and index > 0:
                    break
                update = (
                    {"$unset": {"context.save_sequence": ""}}
                    if legacy == "missing"
                    else {"$set": {"context.save_sequence": -1}}
                )
                await postgres_raw_db.find_one_and_update(
                    "object", {"id": row["id"]}, update
                )
        latest = await store.latest_snapshot(
            run_id=scope.run_id, include_interrupted=True
        )
        assert latest is not None and latest.idempotency_key == "11:0:interrupted"
        listing = await store.list_snapshots(
            run_id=scope.run_id, include_interrupted=True
        )
        assert [item.idempotency_key for item in listing] == [
            *(f"{index}:4:complete" for index in range(11)),
            "11:0:interrupted",
        ]


@pytest.mark.parametrize("legacy", [False, True])
async def test_encrypted_deferred_frontier_can_be_read_in_new_process(
    postgres_raw_db, monkeypatch, legacy
):
    """A scoped frontier survives restart but its original effect stays unresolved."""
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "22" * 32)
    workspace_id = "workspace-" + uuid4().hex
    scope = HarnessExecutionScope(
        tenant_id=workspace_id,
        principal_id="principal-" + uuid4().hex,
        workspace_id=workspace_id,
        thread_id="thread-" + uuid4().hex,
        session_id="conversation-" + uuid4().hex,
        run_id="deferred-" + uuid4().hex,
        permission_revision="permissions-1",
        capability_version="capabilities-1",
    )
    calls = []

    async def stage_document(name: str):
        calls.append(name)
        raise CallDeferred(metadata={"proposal_ref": "synthetic-proposal"})

    async def model(_messages, _info):
        yield {
            0: DeltaToolCall(
                name="stage_document",
                json_args='{"name":"guide.docx"}',
                tool_call_id="call-1",
            )
        }

    worker = """
import asyncio, json, os, sys
from jvspatial.core.context import GraphContext, set_default_context
from jvspatial.db.factory import create_database
from pydantic_ai import Agent
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults
from pydantic_ai_harness.step_persistence import StepPersistence
from app.agentive.harness.contracts import HarnessExecutionScope
from app.agentive.harness.jvspatial_store import JvSpatialStepStore
from app.agentive.harness.scoped_store import ScopedStepStore

async def stage_document(name: str):
    raise AssertionError('deferred call executed again')

async def model(messages, info):
    yield 'Continued.'

async def main():
    scope = HarnessExecutionScope.model_validate_json(sys.argv[1])
    db = create_database(db_type='postgres')
    set_default_context(GraphContext(database=db))
    try:
        store = ScopedStepStore(store=JvSpatialStepStore(scope=scope), scope=scope)
        frontier = await store.latest_snapshot(run_id=scope.run_id, include_interrupted=True)
        assert frontier is not None and frontier.state == 'interrupted'
        assert len(await store.list_unresolved_tool_effects(run_id=scope.run_id)) == 1
        resumed_scope = scope.model_copy(update={'run_id': scope.run_id + '-resume'})
        resumed_store = ScopedStepStore(store=JvSpatialStepStore(scope=resumed_scope), scope=resumed_scope)
        agent = Agent(FunctionModel(stream_function=model), tools=[stage_document],
                      output_type=[str, DeferredToolRequests],
                      capabilities=[StepPersistence(store=resumed_store, run_id=resumed_scope.run_id)])
        events = []
        async with agent.run_stream_events(message_history=frontier.messages,
                conversation_id=scope.session_id,
                deferred_tool_results=DeferredToolResults(calls={'call-1': {'state': 'consumed'}})) as stream:
            async for event in stream:
                events.append(type(event).__name__)
        assert 'AgentRunResultEvent' in events
        assert await resumed_store.latest_snapshot(run_id=resumed_scope.run_id) is not None
        print('__DEFERRED_RESULT__' + json.dumps({'pid': os.getpid(), 'events': events}))
    finally:
        await db.close()

asyncio.run(main())
"""
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        store = ScopedStepStore(store=JvSpatialStepStore(scope=scope), scope=scope)
        agent = Agent(
            FunctionModel(stream_function=model),
            tools=[stage_document],
            output_type=[str, DeferredToolRequests],
            capabilities=[
                StepPersistence(store=store, run_id=scope.run_id, capture_frontier=True)
            ],
        )
        events = []
        async with agent.run_stream_events(
            "File the guide.", conversation_id=scope.session_id
        ) as stream:
            async for event in stream:
                events.append(event)
        assert any(isinstance(event, DeferredToolRequestsEvent) for event in events)
        assert calls == ["guide.docx"]
        if legacy:
            snapshot_rows = await postgres_raw_db.find(
                "object", {"entity": "HarnessSnapshotRecord"}
            )
            for row in snapshot_rows:
                await postgres_raw_db.find_one_and_update(
                    "object", {"id": row["id"]}, {"$set": {"context.save_sequence": -1}}
                )
        encrypted_rows = await postgres_raw_db.find(
            "object", {"entity": "HarnessMessageRecord"}
        )
        assert encrypted_rows
        assert "guide.docx" not in json.dumps(encrypted_rows)
        assert (await store.latest_snapshot(run_id=scope.run_id)).state == "complete"
        frontier = await store.latest_snapshot(
            run_id=scope.run_id, include_interrupted=True
        )
        snapshots = await store.list_snapshots(
            run_id=scope.run_id, include_interrupted=True
        )
        assert frontier.state == "interrupted", [
            (snapshot.state, snapshot.step_index, snapshot.idempotency_key)
            for snapshot in snapshots
        ]
        foreign_scope = scope.model_copy(update={"principal_id": "other-principal"})
        foreign = ScopedStepStore(
            store=JvSpatialStepStore(scope=foreign_scope), scope=foreign_scope
        )
        assert (
            await foreign.latest_snapshot(run_id=scope.run_id, include_interrupted=True)
            is None
        )

        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            worker,
            scope.model_dump_json(),
            env={**os.environ, "PYDANTIC_AI_NO_BANNER": "1"},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=60)
        except BaseException:
            process.kill()
            await process.wait()
            raise
        assert process.returncode == 0, stderr.decode()
        output = [
            line
            for line in stdout.decode().splitlines()
            if line.startswith("__DEFERRED_RESULT__")
        ]
        assert len(output) == 1
        result = json.loads(output[0].removeprefix("__DEFERRED_RESULT__"))
        assert result["pid"] != os.getpid()
        assert len(await store.list_unresolved_tool_effects(run_id=scope.run_id)) == 1
