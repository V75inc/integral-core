"""Deliver only committed native chat events; transport loss does not run work."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable

from app.schemas.agentive.work import TERMINAL_WORK_STATUSES
from app.services.chat_sse import sse_bytes
from app.services.chat_turn_events import replay_work_item_chat_events


async def stream_committed_chat_turn(
    *,
    principal_id: str,
    workspace_id: str,
    thread_id: str,
    work_item_id: str,
    assert_scope: Callable[[], Awaitable[None]],
    after_sequence: int = 0,
    poll_seconds: float = 0.25,
) -> AsyncIterator[bytes]:
    """Replay by sequence and wait for terminal commit without provider dispatch.

    The caller checks owned-thread authority before opening HTTP headers; it is
    rechecked before each delivered page. Cancellation of this iterator only
    closes the transport. Explicit user cancellation uses WorkItem authority.
    """
    cursor = after_sequence
    saw_error = False
    while True:
        await assert_scope()
        page = await replay_work_item_chat_events(
            principal_id=principal_id,
            workspace_id=workspace_id,
            thread_id=thread_id,
            work_item_id=work_item_id,
            after_sequence=cursor,
            limit=200,
        )
        if page.gap:
            yield sse_bytes(
                "error",
                {
                    "type": "error",
                    "code": "chat_event_sequence_gap",
                    "message": "The saved response needs reconciliation. Reload this conversation.",
                },
            )
            yield sse_bytes(
                "turn-settled", {"status": "failed", "work_item_id": work_item_id}
            )
            return
        for event in page.events:
            sequence = event["sequence"]
            payload = {key: value for key, value in event.items() if key != "sequence"}
            saw_error = saw_error or payload.get("type") == "error"
            # SSE event IDs are committed sequence numbers, never provider IDs.
            yield f"id: {sequence}\n".encode() + sse_bytes(
                str(payload["type"]), payload
            )
            cursor = sequence
        if page.has_more:
            continue
        if page.work_status in TERMINAL_WORK_STATUSES:
            if page.work_status not in {"succeeded", "cancelled"} and not saw_error:
                yield sse_bytes(
                    "error",
                    {
                        "type": "error",
                        "code": f"work.{page.work_status}",
                        "message": "This turn stopped before completing. Check its saved result before retrying.",
                    },
                )
            yield sse_bytes(
                "turn-settled",
                {
                    "status": page.work_status,
                    "work_item_id": work_item_id,
                    "committed_through": page.committed_through,
                },
            )
            return
        # A disconnected response never cancels or requeues the accepted item.
        await asyncio.sleep(poll_seconds)
