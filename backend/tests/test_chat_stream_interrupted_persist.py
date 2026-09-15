"""An interrupted turn must not orphan the effects its tools already had.

Observed in the wild: a scheduled routine switched the active thread mid-turn,
the client disconnected, and the ASGI server cancelled the SSE generator at its
``yield``. ``commit_batch`` had already run — a staged change existed, waiting
for approval — but the assistant message carrying its token never persisted, so
the inline approval card had nothing to render from. The change was real and
unreachable from the conversation that produced it.

The cancellation path skipped the persist block entirely: ``except
asyncio.CancelledError: raise`` re-raises before it, leaving only ``finally``.
Everything since the last message-boundary checkpoint was lost.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest

# `app.api` first: chat_streaming -> chat_turn_registry -> app.api.errors pulls
# in the whole api package, whose ai_chat module imports chat_streaming back.
# Importing chat_streaming first would hit that cycle mid-initialisation.
import app.api  # noqa: F401  (import-order guard, not a usage)
from app.services import chat_streaming
from app.services.chat_turn_registry import InFlightTurn


class _FakeThread:
    def __init__(self) -> None:
        self.id = "n.ChatThread.interrupted"
        self.provider_session_id = "sess-interrupted"


class _FakeRequest:
    """Never reports a disconnect — the point is the *external* cancellation."""

    async def is_disconnected(self) -> bool:
        return False


class _SlowProvider:
    """Emits a tool-call, then stalls so the test can cancel mid-turn."""

    def __init__(self, gate: asyncio.Event) -> None:
        self._gate = gate

    async def stream_turn(self, _ctx: Any):
        yield {"type": "text-delta", "delta": "Staging that now."}
        yield {
            "type": "tool-call",
            "toolName": "integral_commit_batch",
            "result": {"_kind": "staged_change", "token": "tok-orphan"},
        }
        self._gate.set()
        # Park here: the turn is mid-flight with an effect already committed.
        await asyncio.sleep(30)
        yield {"type": "message-finish"}


def _make_kwargs(thread: _FakeThread, provider: Any, persisted: List[Dict[str, Any]]):
    async def persist_assistant_drafts(
        _thread, turn_events, *, start_index, end_index, interact_payload
    ):
        persisted.append(
            {
                "events": list(turn_events),
                "start_index": start_index,
                "end_index": end_index,
            }
        )

    async def persist_provider_session_if_needed(_thread, _session):
        return None

    def drafts_from_events(events):
        # One draft per turn is enough for the index arithmetic under test.
        return [{"parts": events}] if events else []

    return {
        "request": _FakeRequest(),
        "thread": thread,
        "user_id": "u1",
        "provider": provider,
        "turn_handle": InFlightTurn(
            turn_id="t1", thread_id=thread.id, user_id="u1", started_at=0.0
        ),
        "turn_ctx": object(),
        "interact_payload": {},
        "drafts_from_events": drafts_from_events,
        "persist_assistant_drafts": persist_assistant_drafts,
        "persist_provider_session_if_needed": persist_provider_session_if_needed,
    }


async def _drain_until(gen, gate: asyncio.Event) -> None:
    async for _chunk in gen:
        if gate.is_set():
            # Keep consuming one more beat so the tool-call event is inside
            # turn_events, then let the caller cancel us.
            await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_cancelled_turn_still_persists_its_tool_results(monkeypatch):
    """Cancel the generator mid-turn; the tool-call event must still land."""
    monkeypatch.setattr(chat_streaming, "_register_cancel_hook", lambda *a, **k: None)

    async def _noop_notify(*_a, **_k):
        return None

    monkeypatch.setattr(chat_streaming, "notify_thread_stream_update", _noop_notify)

    gate = asyncio.Event()
    thread = _FakeThread()
    persisted: List[Dict[str, Any]] = []
    kwargs = _make_kwargs(thread, _SlowProvider(gate), persisted)

    gen = chat_streaming.generate_chat_turn_sse(**kwargs)
    task = asyncio.create_task(_drain_until(gen, gate))

    await asyncio.wait_for(gate.wait(), timeout=5)
    await asyncio.sleep(0.05)  # let the tool-call event be appended

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # The shielded flush runs as its own task; give the loop a beat to finish it.
    await asyncio.sleep(0.1)

    assert persisted, (
        "interrupted turn persisted nothing — the staged token minted by "
        "commit_batch is now orphaned from its conversation"
    )
    events = persisted[-1]["events"]
    tool_calls = [e for e in events if e.get("type") == "tool-call"]
    assert tool_calls, f"tool-call event missing from persisted draft: {events}"
    assert tool_calls[0]["result"]["token"] == "tok-orphan"


@pytest.mark.asyncio
async def test_completed_turn_persists_exactly_once(monkeypatch):
    """The finally-flush must not double-write a turn that ended normally."""
    monkeypatch.setattr(chat_streaming, "_register_cancel_hook", lambda *a, **k: None)

    async def _noop_notify(*_a, **_k):
        return None

    monkeypatch.setattr(chat_streaming, "notify_thread_stream_update", _noop_notify)

    class _QuickProvider:
        async def stream_turn(self, _ctx: Any):
            yield {"type": "text-delta", "delta": "Done."}
            yield {"type": "message-finish"}

    thread = _FakeThread()
    persisted: List[Dict[str, Any]] = []
    kwargs = _make_kwargs(thread, _QuickProvider(), persisted)

    async for _chunk in chat_streaming.generate_chat_turn_sse(**kwargs):
        pass
    await asyncio.sleep(0.05)

    assert len(persisted) == 1, f"expected one persist, got {len(persisted)}"
