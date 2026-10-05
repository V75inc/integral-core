"""PostgreSQL acceptance for fenced native chat worker completion."""

from __future__ import annotations

import asyncio
import multiprocessing
import uuid
from typing import Any

import pytest
from jvspatial.core.context import GraphContext, set_default_context

from app.agentive.services import execution_runs, work_items
from app.agentive.services.execution_runs import AgentRun
from app.agentive.services.work_execution import deterministic_run_id
from app.agentive.services.work_worker import _handle_chat_turn
from app.agentive.work_models import WorkItem
from app.models.edges import CONTAINS, IS_MEMBER_OF
from app.models.nodes import ChatThread
from app.schemas.agentive.work import ChatTurnSubmissionRequest, WorkError
from app.services.chat_threads import create_thread
from app.services.chat_turn_events import replay_work_item_chat_events
from app.services.chat_turn_submissions import submit_chat_turn
from tests.fixtures.workspaces import make_org_workspace


def _claim_from_independent_process(
    work_item_id: str,
    worker_id: str,
    dsn: str,
    barrier: Any,
    results: Any,
) -> None:
    """Open a fresh graph connection and race one PostgreSQL WorkItem claim."""

    async def _claim() -> None:
        from jvspatial.core.context import (
            GraphContext,
            _default_context_var,
            set_default_context,
        )
        from jvspatial.db.factory import create_database

        database = create_database(db_type="postgres", dsn=dsn)
        token = set_default_context(GraphContext(database=database))
        try:
            barrier.wait(timeout=20)
            claimed = await work_items.claim_due_candidate(
                worker_id=worker_id,
                lease_seconds=120,
                work_item_id=work_item_id,
            )
            results.put(
                {
                    "worker_id": worker_id,
                    "claimed": claimed is not None,
                    "lease_fence": claimed.lease_fence if claimed is not None else None,
                }
            )
        finally:
            _default_context_var.reset(token)
            await database.close()

    try:
        asyncio.run(_claim())
    except BaseException as exc:  # report child errors to the owning pytest process
        results.put({"worker_id": worker_id, "error": f"{type(exc).__name__}: {exc}"})


def _claim_and_exit_abruptly(
    work_item_id: str,
    worker_id: str,
    dsn: str,
    lease_seconds: float,
) -> None:
    """Persist a lease in a child process, then die without cleanup."""

    async def _claim() -> bool:
        from jvspatial.core.context import GraphContext, set_default_context
        from jvspatial.db.factory import create_database

        database = create_database(db_type="postgres", dsn=dsn)
        set_default_context(GraphContext(database=database))
        claimed = await work_items.claim_due_candidate(
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            work_item_id=work_item_id,
        )
        return claimed is not None

    import os

    claimed = asyncio.run(_claim())
    # Exit only after claim_due_candidate committed. Deliberately bypass normal
    # async database cleanup to model a worker process crash after claim.
    os._exit(77 if claimed else 78)


def _persist_model_dispatch_and_exit_abruptly(
    work_item_id: str,
    dsn: str,
    outcomes: tuple[str, ...],
) -> None:
    """Commit model-attempt uncertainty in a worker process, then die."""

    async def _dispatch() -> None:
        from datetime import datetime, timedelta, timezone

        from jvspatial.core.context import GraphContext, set_default_context
        from jvspatial.db.factory import create_database

        from app.agentive.harness.contracts import (
            HarnessExecutionScope,
            PhysicalModelRequest,
        )
        from app.agentive.harness.model_observations import (
            persist_model_request_observation,
        )
        from app.agentive.services.work_execution import (
            build_work_execution_context,
            logical_step_key_for,
        )

        database = create_database(db_type="postgres", dsn=dsn)
        set_default_context(GraphContext(database=database))
        item = await WorkItem.get(f"o.WorkItem.{work_item_id}")
        if item is None:
            raise RuntimeError("chat WorkItem disappeared before dispatch")
        execution = build_work_execution_context(
            work_item=item,
            logical_step_key=logical_step_key_for(kind="chat_turn"),
        )
        scope = HarnessExecutionScope(
            tenant_id=item.workspace_id,
            principal_id=item.principal_id,
            workspace_id=item.workspace_id,
            thread_id=item.thread_id,
            session_id=f"session-{item.thread_id}",
            run_id=execution.run_id,
            permission_revision="permissions-v1",
            capability_version="capabilities-v1",
        )
        dispatched_at = datetime.now(timezone.utc)
        request_id = f"process-crash-{uuid.uuid4().hex}"
        for index, outcome in enumerate(outcomes):
            await persist_model_request_observation(
                PhysicalModelRequest(
                    request_id=request_id,
                    scope=scope,
                    provider="openai",
                    model="openai/gpt-4.1-mini",
                    attempt=1,
                    dispatched_at=dispatched_at,
                    observed_at=dispatched_at + timedelta(milliseconds=index + 1),
                    outcome=outcome,
                ),
                work_execution_context=execution,
            )

        # Bypass async cleanup only after PostgreSQL acknowledged the durable
        # observations, matching a worker crash at the provider boundary.
        import os

        os._exit(79)

    try:
        asyncio.run(_dispatch())
    except BaseException:
        import os

        os._exit(80)


@pytest.fixture
def postgres_graph_context(postgres_raw_db):
    """Bind graph operations to the isolated PostgreSQL contract database."""
    from jvspatial.core.context import _default_context_var

    token = set_default_context(GraphContext(database=postgres_raw_db))
    try:
        yield
    finally:
        _default_context_var.reset(token)


async def _submitted_claimed_turn() -> tuple[WorkItem, str]:
    """Create a real scoped user message, encrypted capsule, and lease."""
    from app.services.app_graph import catalog_workspace, ensure_integral_app_graph

    await ensure_integral_app_graph(include_library=False)
    suffix = uuid.uuid4().hex
    workspace = await make_org_workspace(f"native-worker-{suffix}")
    await catalog_workspace(workspace)
    owners = await workspace.nodes(
        edge=[IS_MEMBER_OF], node=["User"], direction="in", limit=5
    )
    assert owners
    owner_id = owners[0].id
    thread = await create_thread(
        user_id=owner_id,
        workspace_id=workspace.id,
        provider_id="integral_native",
    )
    receipt = await submit_chat_turn(
        ChatTurnSubmissionRequest(
            principal_id=owner_id,
            workspace_id=workspace.id,
            thread_id=thread.id,
            client_request_id=f"request-{suffix}",
            parts=[{"type": "text", "text": "Give a concise answer."}],
        )
    )
    claimed = await work_items.claim_due_candidate(
        worker_id=f"worker-{suffix}",
        lease_seconds=120,
        work_item_id=receipt.work_item_id,
    )
    assert claimed is not None
    assert claimed.deadline_at
    return claimed, owner_id


class _Provider:
    """Deterministic provider double for worker persistence tests."""

    def __init__(self, events: list[dict[str, Any]]):
        self.events = events
        self.calls = 0

    def is_available(self) -> bool:
        return True

    async def stream_turn(self, _turn):
        self.calls += 1
        for event in self.events:
            if isinstance(event, BaseException):
                raise event
            yield event


class _HangingProvider(_Provider):
    """Provider double that emits partial output, then never closes."""

    def __init__(self):
        super().__init__([])
        self.cancelled = False

    async def stream_turn(self, _turn):
        self.calls += 1
        yield {"type": "text-delta", "delta": "Partial answer."}
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class _Registry:
    def __init__(self, provider: _Provider):
        self.provider = provider

    def get(self, provider_id: str):
        return self.provider if provider_id == "integral_native" else None


async def _fake_start_run(**fields):
    """Persist the run identity needed by the real atomic terminalizer."""
    return await AgentRun.create(
        run_id=fields["run_id"],
        thread_id=fields["thread_id"],
        user_id=fields["user_id"],
        workspace_id=fields["workspace_id"],
        provider_id=fields["provider_id"],
        origin=fields["origin"],
        status="running",
        work_item_id=fields["work_item_id"],
        metadata=fields["metadata"],
        started_at="2026-10-05T00:00:00+00:00",
    )


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_worker_commits_events_and_one_usage_bearing_transcript(
    postgres_graph_context, monkeypatch
) -> None:
    """The durable worker commits replayable output before succeeding once."""
    from app.services import chat_providers

    item, principal_id = await _submitted_claimed_turn()
    provider = _Provider(
        [
            {"type": "_meta", "provider_session_id": "private-session"},
            {"type": "text-delta", "delta": "Ready."},
            {
                "type": "step",
                "modelId": "control-model",
                "provider": "test",
                "usage": {"inputTokens": 9, "outputTokens": 2},
                "requestId": "request-observation-1",
                "attempt": 1,
            },
            {
                "type": "message-finish",
                "timing": {"totalMs": 40.0, "firstTokenMs": 8.0},
            },
        ]
    )
    monkeypatch.setattr(chat_providers, "get_registry", lambda: _Registry(provider))
    monkeypatch.setattr(execution_runs, "start_run", _fake_start_run)

    finished = await _handle_chat_turn(item, worker_id="worker-1", lease_seconds=120)

    assert finished.status == "succeeded"
    assert provider.calls == 1
    page = await replay_work_item_chat_events(
        principal_id=principal_id,
        workspace_id=item.workspace_id,
        thread_id=item.thread_id,
        work_item_id=item.work_item_id,
    )
    assert page.gap is False
    assert [event["sequence"] for event in page.events] == [1, 2, 3]
    assert all("provider_session_id" not in event for event in page.events)
    thread = await ChatThread.get(item.thread_id)
    assert thread is not None
    messages = await thread.nodes(
        edge=[CONTAINS], node=["ChatMessage"], direction="out", limit=10
    )
    assistant = [message for message in messages if message.role == "assistant"]
    assert len(assistant) == 1
    assert assistant[0].parts == [{"type": "text", "text": "Ready."}]
    assert assistant[0].provider_metadata["steps"][0]["usage"] == {
        "inputTokens": 9,
        "outputTokens": 2,
    }
    run = await AgentRun.find_one(
        {
            "run_id": deterministic_run_id(
                work_item_id=item.work_item_id,
                attempt=item.attempt,
            )
        }
    )
    assert run is not None
    assert run.metadata == {
        "work_item_id": item.work_item_id,
        "chat_message_id": item.input_payload["accepted_message_id"],
    }
    assert "Give a concise answer." not in repr(run.metadata)


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_worker_fails_closed_when_provider_stream_has_no_finish_event(
    postgres_graph_context, monkeypatch
) -> None:
    """An abruptly ended stream is terminally recorded, never shown as success."""
    from app.services import chat_providers

    item, principal_id = await _submitted_claimed_turn()
    provider = _Provider([{"type": "text-delta", "delta": "Partial answer."}])
    monkeypatch.setattr(chat_providers, "get_registry", lambda: _Registry(provider))
    monkeypatch.setattr(execution_runs, "start_run", _fake_start_run)

    finished = await _handle_chat_turn(
        item, worker_id="worker-incomplete", lease_seconds=120
    )

    assert finished.status == "failed"
    assert provider.calls == 1
    page = await replay_work_item_chat_events(
        principal_id=principal_id,
        workspace_id=item.workspace_id,
        thread_id=item.thread_id,
        work_item_id=item.work_item_id,
    )
    assert [event["type"] for event in page.events] == ["text-delta", "error"]
    assert page.events[-1]["code"] == "work.chat_stream_incomplete"
    thread = await ChatThread.get(item.thread_id)
    assert thread is not None
    messages = await thread.nodes(
        edge=[CONTAINS], node=["ChatMessage"], direction="out", limit=10
    )
    assistant = [message for message in messages if message.role == "assistant"]
    assert len(assistant) == 1
    assert assistant[0].parts == [
        {"type": "text", "text": "Partial answer."},
        {
            "type": "error",
            "code": "work.chat_stream_incomplete",
            "message": (
                "The assistant stream ended before the turn was complete. "
                "Start a new message to try again."
            ),
        },
    ]


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_worker_times_out_and_cancels_a_stalled_provider_stream(
    postgres_graph_context, monkeypatch
) -> None:
    """A hung stream is terminalized without leaving its provider task alive."""
    from app.config import settings
    from app.services import chat_providers

    item, principal_id = await _submitted_claimed_turn()
    provider = _HangingProvider()
    monkeypatch.setattr(settings, "INTEGRAL_HARNESS_CHAT_TURN_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(chat_providers, "get_registry", lambda: _Registry(provider))
    monkeypatch.setattr(execution_runs, "start_run", _fake_start_run)

    finished = await _handle_chat_turn(
        item, worker_id="worker-timeout", lease_seconds=120
    )

    assert finished.status == "failed"
    assert finished.failure is not None
    assert finished.failure["code"] == "work.chat_stream_timeout"
    assert provider.calls == 1
    assert provider.cancelled is True
    page = await replay_work_item_chat_events(
        principal_id=principal_id,
        workspace_id=item.workspace_id,
        thread_id=item.thread_id,
        work_item_id=item.work_item_id,
    )
    assert [event["type"] for event in page.events] == [
        "text-delta",
        "error",
    ]
    assert page.events[-1]["code"] == "work.chat_stream_timeout"
    thread = await ChatThread.get(item.thread_id)
    assert thread is not None
    messages = await thread.nodes(
        edge=[CONTAINS], node=["ChatMessage"], direction="out", limit=10
    )
    assistant = [message for message in messages if message.role == "assistant"]
    assert len(assistant) == 1
    assert assistant[0].parts[-1]["code"] == "work.chat_stream_timeout"


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_independent_worker_processes_claim_one_chat_turn_once(
    postgres_graph_context,
) -> None:
    """Separate OS processes cannot both acquire one durable chat lease."""
    import os

    item = await work_items.enqueue_work_item(
        kind="chat_turn",
        origin="interactive_chat",
        principal_id="multi-process-principal",
        workspace_id="multi-process-workspace",
        thread_id="multi-process-thread",
        idempotency_key=f"multi-process-{uuid.uuid4().hex}",
        input_payload={"accepted_message_id": "n.ChatMessage.test"},
    )

    process_context = multiprocessing.get_context("spawn")
    barrier = process_context.Barrier(2)
    results = process_context.Queue()
    dsn = os.environ["JVSPATIAL_POSTGRES_DSN"]
    processes = [
        process_context.Process(
            target=_claim_from_independent_process,
            args=(item.work_item_id, f"process-worker-{index}", dsn, barrier, results),
        )
        for index in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=30)
    for process in processes:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)

    assert [process.exitcode for process in processes] == [0, 0]
    outcomes = [results.get(timeout=3) for _ in processes]
    assert not [outcome for outcome in outcomes if "error" in outcome]
    winners = [outcome for outcome in outcomes if outcome["claimed"]]
    assert len(winners) == 1
    assert winners[0]["lease_fence"] == 1

    from jvspatial.core.context import get_default_context

    context = get_default_context()
    await context._evict_from_cache(item.id)
    persisted = await WorkItem.get(item.id)
    assert persisted is not None
    assert persisted.status == "running"
    assert persisted.lease_owner == winners[0]["worker_id"]
    assert persisted.lease_fence == 1
    assert persisted.attempt == 1


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_chat_turn_claim_survives_process_death_and_reclaims_with_new_fence(
    postgres_graph_context,
) -> None:
    """A dead process leaves durable work reclaimable under a higher fence."""
    import os

    item = await work_items.enqueue_work_item(
        kind="chat_turn",
        origin="interactive_chat",
        principal_id="crashed-worker-principal",
        workspace_id="crashed-worker-workspace",
        thread_id="crashed-worker-thread",
        idempotency_key=f"crashed-worker-{uuid.uuid4().hex}",
        input_payload={"accepted_message_id": "n.ChatMessage.test"},
    )
    process_context = multiprocessing.get_context("spawn")
    process = process_context.Process(
        target=_claim_and_exit_abruptly,
        args=(
            item.work_item_id,
            "crashed-chat-worker",
            os.environ["JVSPATIAL_POSTGRES_DSN"],
            120,
        ),
    )
    process.start()
    process.join(timeout=30)
    if process.is_alive():
        process.terminate()
        process.join(timeout=5)

    assert process.exitcode == 77
    from jvspatial.core.context import get_default_context

    context = get_default_context()
    await context._evict_from_cache(item.id)
    crashed = await WorkItem.get(item.id)
    assert crashed is not None
    assert crashed.status == "running"
    assert crashed.lease_owner == "crashed-chat-worker"
    assert crashed.lease_fence == 1

    await work_items.force_expire_lease_for_tests(item.work_item_id)
    recovered = await work_items.reclaim_expired_lease(
        item.work_item_id,
        worker_id="chat-worker-recovery",
        lease_seconds=120,
    )
    assert recovered.status == "running"
    assert recovered.lease_owner == "chat-worker-recovery"
    assert recovered.lease_fence == 2


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcomes",
    [
        ("dispatch_intent",),
        ("dispatch_intent", "outcome_unknown"),
    ],
    ids=["crash-after-dispatch-intent", "interrupted-provider-stream"],
)
async def test_unsettled_model_request_blocks_native_chat_replay(
    postgres_graph_context,
    monkeypatch,
    outcomes: tuple[str, ...],
) -> None:
    """Persisted dispatch ambiguity prevents checkpoint or paid-request replay."""
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace

    from app.agentive.harness.contracts import (
        HarnessExecutionScope,
        PhysicalModelRequest,
    )
    from app.agentive.harness.model_observations import (
        persist_model_request_observation,
    )
    from app.agentive.services import work_execution
    from app.api.errors import ResourceConflictError
    from app.services.chat_providers import pydantic_ai_provider

    monkeypatch.setattr(
        "app.services.credential_crypto._resolve_key", lambda: (b"k" * 32, None)
    )
    item, _principal_id = await _submitted_claimed_turn()
    context = work_execution.build_work_execution_context(
        work_item=item,
        logical_step_key=work_execution.logical_step_key_for(kind="chat_turn"),
    )
    scope = HarnessExecutionScope(
        tenant_id=item.workspace_id,
        principal_id=item.principal_id,
        workspace_id=item.workspace_id,
        thread_id=item.thread_id,
        session_id=f"session-{item.thread_id}",
        run_id=context.run_id,
        permission_revision="permissions-v1",
        capability_version="capabilities-v1",
    )
    dispatched_at = datetime.now(timezone.utc)
    request_id = f"physical-request-{uuid.uuid4().hex}"
    for index, outcome in enumerate(outcomes):
        await persist_model_request_observation(
            PhysicalModelRequest(
                request_id=request_id,
                scope=scope,
                provider="openai",
                model="openai/gpt-4.1-mini",
                attempt=1,
                dispatched_at=dispatched_at,
                observed_at=dispatched_at + timedelta(milliseconds=index + 1),
                outcome=outcome,
            ),
            work_execution_context=context,
        )

    async def checkpoint_must_not_load(*_args, **_kwargs):
        pytest.fail("unsettled paid request must block checkpoint replay")

    monkeypatch.setattr(pydantic_ai_provider, "continue_run", checkpoint_must_not_load)
    with pytest.raises(ResourceConflictError) as error:
        await pydantic_ai_provider._resume_history(
            scope.model_copy(update={"run_id": "recovery-attempt"}),
            SimpleNamespace(last_run_id=context.run_id),
            SimpleNamespace(),
        )

    assert error.value.details["reason"] == "harness_model_request_unsettled"
    assert error.value.details["request_count"] == 1


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcomes",
    [
        ("dispatch_intent",),
        ("dispatch_intent", "outcome_unknown"),
    ],
    ids=["worker-crash-after-dispatch", "worker-crash-during-stream"],
)
async def test_process_crash_preserves_unsettled_dispatch_and_blocks_replay(
    postgres_graph_context,
    outcomes: tuple[str, ...],
) -> None:
    """A separate worker's committed dispatch intent survives process death."""
    import os
    from types import SimpleNamespace

    from app.agentive.harness.contracts import HarnessExecutionScope
    from app.agentive.harness.model_observations import (
        list_model_request_observations,
        unsettled_model_request_ids,
    )
    from app.agentive.services import work_execution
    from app.api.errors import ResourceConflictError
    from app.services.chat_providers import pydantic_ai_provider

    item, _principal_id = await _submitted_claimed_turn()
    process = multiprocessing.get_context("spawn").Process(
        target=_persist_model_dispatch_and_exit_abruptly,
        args=(item.work_item_id, os.environ["JVSPATIAL_POSTGRES_DSN"], outcomes),
    )
    process.start()
    process.join(timeout=30)
    if process.is_alive():
        process.terminate()
        process.join(timeout=5)

    assert process.exitcode == 79
    execution = work_execution.build_work_execution_context(
        work_item=item,
        logical_step_key=work_execution.logical_step_key_for(kind="chat_turn"),
    )
    scope = HarnessExecutionScope(
        tenant_id=item.workspace_id,
        principal_id=item.principal_id,
        workspace_id=item.workspace_id,
        thread_id=item.thread_id,
        session_id=f"session-{item.thread_id}",
        run_id=execution.run_id,
        permission_revision="permissions-v1",
        capability_version="capabilities-v1",
    )
    observations = await list_model_request_observations(scope=scope)
    assert len(unsettled_model_request_ids(observations)) == 1

    async def checkpoint_must_not_load(*_args, **_kwargs):
        pytest.fail("process-crashed dispatch must block checkpoint replay")

    with pytest.raises(ResourceConflictError) as error:
        await pydantic_ai_provider._resume_history(
            scope,
            SimpleNamespace(last_run_id=execution.run_id),
            SimpleNamespace(continue_run=checkpoint_must_not_load),
        )

    assert error.value.details["reason"] == "harness_model_request_unsettled"
    assert error.value.details["request_count"] == 1


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_cancelled_worker_persists_committed_partial_output(
    postgres_graph_context, monkeypatch
) -> None:
    """A stopped stream retains partial text and one terminal assistant result."""
    from app.services import chat_providers

    item, principal_id = await _submitted_claimed_turn()
    provider = _Provider(
        [
            {"type": "text-delta", "delta": "Partial answer"},
            WorkError("work.cancelled", "durable cancellation stopped the handler"),
        ]
    )
    monkeypatch.setattr(chat_providers, "get_registry", lambda: _Registry(provider))
    monkeypatch.setattr(execution_runs, "start_run", _fake_start_run)

    finished = await _handle_chat_turn(item, worker_id="worker-2", lease_seconds=120)

    assert finished.status == "cancelled"
    page = await replay_work_item_chat_events(
        principal_id=principal_id,
        workspace_id=item.workspace_id,
        thread_id=item.thread_id,
        work_item_id=item.work_item_id,
    )
    assert [event["type"] for event in page.events] == ["text-delta", "error"]
    thread = await ChatThread.get(item.thread_id)
    assert thread is not None
    messages = await thread.nodes(
        edge=[CONTAINS], node=["ChatMessage"], direction="out", limit=10
    )
    assistant = [message for message in messages if message.role == "assistant"]
    assert len(assistant) == 1
    assert assistant[0].parts == [
        {"type": "text", "text": "Partial answer"},
        {
            "type": "error",
            "code": "work.cancelled",
            "message": "durable cancellation stopped the handler",
        },
    ]
    assert provider.calls == 1


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_retry_after_transcript_write_does_not_repeat_model_request(
    postgres_graph_context, monkeypatch
) -> None:
    """A crash after saving the result resumes terminalization from durable data."""
    from app.services import chat_providers, chat_turn_worker

    item, _principal_id = await _submitted_claimed_turn()
    provider = _Provider(
        [
            {"type": "text-delta", "delta": "Completed once."},
            {
                "type": "message-finish",
                "timing": {"totalMs": 25.0, "firstTokenMs": 7.0},
            },
        ]
    )
    monkeypatch.setattr(chat_providers, "get_registry", lambda: _Registry(provider))
    monkeypatch.setattr(execution_runs, "start_run", _fake_start_run)
    terminalize = chat_turn_worker.terminalize_chat_turn
    terminalize_attempts = 0

    async def crash_after_transcript(*args: Any, **kwargs: Any) -> Any:
        nonlocal terminalize_attempts
        terminalize_attempts += 1
        if terminalize_attempts == 1:
            raise RuntimeError("simulated worker death after transcript persistence")
        return await terminalize(*args, **kwargs)

    monkeypatch.setattr(
        chat_turn_worker, "terminalize_chat_turn", crash_after_transcript
    )
    with pytest.raises(RuntimeError, match="simulated worker death"):
        await _handle_chat_turn(item, worker_id="worker-crash", lease_seconds=120)

    finished = await _handle_chat_turn(
        item, worker_id="worker-recovery", lease_seconds=120
    )

    assert finished.status == "succeeded"
    assert provider.calls == 1
    thread = await ChatThread.get(item.thread_id)
    assert thread is not None
    messages = await thread.nodes(
        edge=[CONTAINS], node=["ChatMessage"], direction="out", limit=10
    )
    assistant = [message for message in messages if message.role == "assistant"]
    assert len(assistant) == 1
    assert assistant[0].parts == [{"type": "text", "text": "Completed once."}]


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_retry_after_terminal_event_does_not_repeat_model_request(
    postgres_graph_context, monkeypatch
) -> None:
    """A committed finish event is enough to avoid repeating provider work."""
    from app.services import chat_providers, chat_turn_transcript

    item, _principal_id = await _submitted_claimed_turn()
    provider = _Provider(
        [
            {"type": "text-delta", "delta": "Finished before the crash."},
            {"type": "message-finish", "timing": {"totalMs": 31.0}},
        ]
    )
    monkeypatch.setattr(chat_providers, "get_registry", lambda: _Registry(provider))
    monkeypatch.setattr(execution_runs, "start_run", _fake_start_run)
    persist_result = chat_turn_transcript.persist_work_item_assistant_result
    persist_attempts = 0

    async def crash_before_transcript(*args: Any, **kwargs: Any) -> Any:
        nonlocal persist_attempts
        persist_attempts += 1
        if persist_attempts == 1:
            raise RuntimeError("simulated worker death after terminal event")
        return await persist_result(*args, **kwargs)

    monkeypatch.setattr(
        chat_turn_transcript,
        "persist_work_item_assistant_result",
        crash_before_transcript,
    )
    with pytest.raises(RuntimeError, match="after terminal event"):
        await _handle_chat_turn(item, worker_id="worker-crash", lease_seconds=120)

    finished = await _handle_chat_turn(
        item, worker_id="worker-recovery", lease_seconds=120
    )

    assert finished.status == "succeeded"
    assert provider.calls == 1
    assert persist_attempts == 2
