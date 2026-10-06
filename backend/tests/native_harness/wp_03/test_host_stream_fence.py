"""The host chat stream must fence transcript writes for durable WorkItems."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

# Preserve the repository's app.api -> chat_streaming import-order contract.
import app.api  # noqa: F401
from app.schemas.agentive.work import WorkError, WorkExecutionContext
from app.services import chat_streaming
from app.services.chat_turn_registry import InFlightTurn


def _authority() -> WorkExecutionContext:
    return WorkExecutionContext(
        work_item_id="work-host-stream",
        attempt=1,
        run_id="run-host-stream",
        principal_id="user-host-stream",
        workspace_id="workspace-host-stream",
        thread_id="thread-host-stream",
        logical_step_key="native-harness:0",
        effect_key="effect-host-stream",
        lease_token="secret-host-stream-token",
        lease_fence=1,
    )


def _kwargs(provider: Any, authority: WorkExecutionContext):
    persisted: List[Dict[str, Any]] = []
    thread = SimpleNamespace(id="thread-host-stream", provider_session_id=None)

    async def persist_assistant_drafts(
        _thread, events, *, start_index, end_index, interact_payload
    ):
        persisted.append(
            {
                "events": list(events),
                "start_index": start_index,
                "end_index": end_index,
            }
        )

    async def persist_provider_session(_thread, _session_id):
        return None

    async def notify(*_args: Any, **_kwargs: Any) -> None:
        return None

    kwargs = {
        "request": SimpleNamespace(is_disconnected=_never_disconnected),
        "thread": thread,
        "user_id": authority.principal_id,
        "provider": provider,
        "turn_handle": InFlightTurn(
            turn_id="turn-host-stream",
            thread_id=thread.id,
            user_id=authority.principal_id,
            started_at=0.0,
        ),
        "turn_ctx": SimpleNamespace(
            extra_data={"work_execution_context": authority.model_dump()}
        ),
        "interact_payload": {},
        "drafts_from_events": lambda events: (
            [{"parts": list(events)}] if events else []
        ),
        "persist_assistant_drafts": persist_assistant_drafts,
        "persist_provider_session_if_needed": persist_provider_session,
    }
    return kwargs, persisted


async def _never_disconnected() -> bool:
    return False


def _quiet_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chat_streaming, "_register_cancel_hook", lambda *_a, **_k: None)
    monkeypatch.setattr(chat_streaming, "notify_thread_stream_update", _quiet_notify)

    async def release_turn(_thread_id: str) -> None:
        return None

    monkeypatch.setattr(chat_streaming.chat_turn_registry, "release_turn", release_turn)


async def _quiet_notify(*_args: Any, **_kwargs: Any) -> None:
    return None


@pytest.mark.asyncio
async def test_lost_workitem_authority_blocks_late_host_output_and_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lease loss between provider events must skip every transcript flush."""
    from app.agentive.services import work_items

    _quiet_host(monkeypatch)
    authority = _authority()
    checks = 0

    async def assert_current(_context: WorkExecutionContext) -> None:
        nonlocal checks
        checks += 1
        if checks == 4:
            raise WorkError("work.lease_lost", "execution lease is no longer current")

    @asynccontextmanager
    async def authorized_effect(context: WorkExecutionContext | None):
        assert context == authority
        yield None

    monkeypatch.setattr(
        work_items, "assert_work_item_execution_current", assert_current
    )
    monkeypatch.setattr(
        chat_streaming, "_authorized_work_item_effect", authorized_effect
    )

    class Provider:
        async def stream_turn(self, _ctx: Any):
            yield {"type": "text-delta", "delta": "first output "}
            yield {"type": "text-delta", "delta": "late output"}

    kwargs, persisted = _kwargs(Provider(), authority)
    stream = chat_streaming.generate_chat_turn_sse(**kwargs)
    chunks: List[bytes] = []
    with pytest.raises(WorkError) as exc_info:
        async for chunk in stream:
            chunks.append(chunk)

    assert exc_info.value.code == "work.lease_lost"
    assert authority.lease_token not in str(exc_info.value)
    assert any(b"first output" in chunk for chunk in chunks)
    assert all(b"late output" not in chunk for chunk in chunks)
    assert persisted == []


@pytest.mark.asyncio
async def test_workitem_assistant_transcript_flush_runs_inside_fenced_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The assistant transcript callback shares the WorkItem effect scope."""
    from app.agentive.services import work_items

    _quiet_host(monkeypatch)
    authority = _authority()
    effect_depth = 0
    callbacks_seen_inside_effect: List[str] = []

    async def assert_current(_context: WorkExecutionContext) -> None:
        return None

    @asynccontextmanager
    async def authorized_effect(context: WorkExecutionContext | None):
        nonlocal effect_depth
        assert context == authority
        effect_depth += 1
        try:
            yield None
        finally:
            effect_depth -= 1

    monkeypatch.setattr(
        work_items, "assert_work_item_execution_current", assert_current
    )
    monkeypatch.setattr(
        chat_streaming, "_authorized_work_item_effect", authorized_effect
    )

    class Provider:
        async def stream_turn(self, _ctx: Any):
            yield {"type": "text-delta", "delta": "assistant answer "}
            yield {"type": "message-finish"}

    kwargs, persisted = _kwargs(Provider(), authority)
    original_persist = kwargs["persist_assistant_drafts"]
    original_session = kwargs["persist_provider_session_if_needed"]

    async def persist_assistant(*args: Any, **options: Any) -> None:
        assert effect_depth > 0
        callbacks_seen_inside_effect.append("assistant_transcript")
        await original_persist(*args, **options)

    async def persist_session(*args: Any, **options: Any) -> None:
        assert effect_depth > 0
        callbacks_seen_inside_effect.append("provider_session")
        await original_session(*args, **options)

    kwargs["persist_assistant_drafts"] = persist_assistant
    kwargs["persist_provider_session_if_needed"] = persist_session
    async for _chunk in chat_streaming.generate_chat_turn_sse(**kwargs):
        pass

    assert callbacks_seen_inside_effect == ["assistant_transcript"]
    assert len(persisted) == 1
