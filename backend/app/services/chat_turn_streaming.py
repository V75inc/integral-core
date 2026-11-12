"""Deliver only committed native chat events; transport loss does not run work."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable

from app.schemas.agentive.work import TERMINAL_WORK_STATUSES
from app.services.chat_sse import sse_bytes
from app.services.chat_turn_events import (
    chat_approval_error,
    replay_work_item_chat_events,
)


async def find_parked_chat_stream_id(
    *, principal_id: str, workspace_id: str, thread_id: str, thread_revision: str
) -> str | None:
    """Discover a continuation stream without reserving an execution slot."""
    from jvspatial.core.context import get_default_context

    database = get_default_context().database
    approvals = await database.find(
        "object",
        {
            "entity": "WorkApproval",
            "context.deferred_thread_revision": thread_revision,
            "context.status": {"$in": ["pending", "approved"]},
            "context.framework_snapshot_id": {"$exists": True, "$ne": ""},
        },
        limit=2,
    )
    if len(approvals) != 1:
        return None
    approval = approvals[0].get("context") or {}
    work_item_id = approval.get("work_item_id")
    run_id = approval.get("run_id")
    if not isinstance(work_item_id, str) or not work_item_id or not run_id:
        return None
    row = await database.get("object", f"o.WorkItem.{work_item_id}")
    item = (row or {}).get("context") or {}
    if (
        (row or {}).get("entity") == "WorkItem"
        and item.get("work_item_id") == work_item_id
        and item.get("kind") == "chat_turn"
        and item.get("principal_id") == principal_id
        and item.get("workspace_id") == workspace_id
        and item.get("thread_id") == thread_id
        and item.get("run_id") == run_id
        and item.get("status") in {"waiting_for_human", "queued", "running"}
    ):
        return work_item_id
    return None


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
    rechecked before every frame offered to the transport. Frames already
    admitted to a stalled transport cannot be recalled; a database transaction
    is never held across network delivery. Cancellation of this iterator only
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
            await assert_scope()
            yield sse_bytes(
                "error",
                {
                    "type": "error",
                    "code": "chat_event_sequence_gap",
                    "message": "The saved response needs reconciliation. Reload this conversation.",
                },
            )
            await assert_scope()
            yield sse_bytes(
                "turn-settled", {"status": "failed", "work_item_id": work_item_id}
            )
            return
        for event in page.events:
            await assert_scope()
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
                error = (
                    chat_approval_error(page.approval_outcome)
                    if page.approval_outcome
                    else {
                        "type": "error",
                        "code": f"work.{page.work_status}",
                        "message": "This turn stopped before completing. Check its saved result before retrying.",
                    }
                )
                await assert_scope()
                yield sse_bytes("error", error)
            await assert_scope()
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
