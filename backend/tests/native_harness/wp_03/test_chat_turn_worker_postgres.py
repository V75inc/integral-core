"""PostgreSQL acceptance for fenced native chat worker completion."""

from __future__ import annotations

import asyncio
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
