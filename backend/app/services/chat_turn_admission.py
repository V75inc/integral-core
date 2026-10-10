"""PostgreSQL-shared admission slots for native interactive chat turns."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from app.agentive.work_models import WorkItem
from app.api.errors import ResourceConflictError
from app.models.nodes import ChatThread
from app.schemas.agentive.work import TERMINAL_WORK_STATUSES, WorkError

OBJECT_COLLECTION = "object"
NODE_COLLECTION = "node"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_updated_at(previous: str | None) -> str:
    now = datetime.now(timezone.utc)
    if previous:
        prior = datetime.fromisoformat(previous.replace("Z", "+00:00"))
        if prior.tzinfo is None:
            prior = prior.replace(tzinfo=timezone.utc)
        if now <= prior:
            now = prior + timedelta(microseconds=1)
    return now.isoformat()


def _slot_id(principal_id: str, slot_index: int) -> str:
    digest = hashlib.sha256(principal_id.encode("utf-8")).hexdigest()
    return f"o.HarnessTurnAdmissionSlot.{digest}.{slot_index}"


def _slot_document(
    *, slot_id: str, principal_id: str, slot_index: int
) -> dict[str, Any]:
    now = _now()
    return {
        "id": slot_id,
        "entity": "HarnessTurnAdmissionSlot",
        "context": {
            "principal_id": principal_id,
            "slot_index": slot_index,
            "workspace_id": "",
            "thread_id": "",
            "work_item_id": "",
            "acquired_at": "",
            "updated_at": now,
        },
    }


async def reserve_chat_turn_admission(
    *,
    transaction: Any,
    thread: ChatThread,
    principal_id: str,
    workspace_id: str,
    work_item: WorkItem,
) -> None:
    """Atomically reserve a principal permit and the thread's active WorkItem.

    Caller owns the PostgreSQL transaction that also creates the accepted
    message, WorkItem and outbox. Replays of the same active WorkItem are
    idempotent. A distinct active WorkItem cannot hold either reservation.
    """
    from app.config import settings

    if work_item.status in TERMINAL_WORK_STATUSES:
        await release_chat_turn_admission_in_transaction(
            transaction=transaction,
            thread=thread,
            work_item_id=work_item.work_item_id,
        )
        return

    current_id = str(getattr(thread, "active_work_item_id", "") or "")
    if current_id and current_id != work_item.work_item_id:
        current = await WorkItem.get(f"o.WorkItem.{current_id}")
        if current is None or current.status not in TERMINAL_WORK_STATUSES:
            raise ResourceConflictError(
                message="A turn is already in progress on this thread",
                details={"thread_id": thread.id, "reason": "thread_busy"},
            )

    limit = max(int(getattr(settings, "MAX_CONCURRENT_TURNS_PER_USER", 5)), 1)
    selected_slot: str | None = None
    for index in range(limit):
        slot_id = _slot_id(principal_id, index)
        await transaction.insert_if_absent(
            OBJECT_COLLECTION,
            _slot_document(
                slot_id=slot_id,
                principal_id=principal_id,
                slot_index=index,
            ),
        )
        slot = await transaction.get(OBJECT_COLLECTION, slot_id)
        if slot is None:
            raise WorkError("work.admission_slot_missing", "admission slot missing")
        context = slot.get("context") or {}
        held_by = str(context.get("work_item_id") or "")
        if held_by == work_item.work_item_id:
            selected_slot = slot_id
            break
        if held_by:
            prior = await WorkItem.get(f"o.WorkItem.{held_by}")
            if prior is not None and prior.status not in TERMINAL_WORK_STATUSES:
                continue
            released = await transaction.find_one_and_update(
                OBJECT_COLLECTION,
                {"id": slot_id, "context.work_item_id": held_by},
                {
                    "$set": {
                        "context.workspace_id": "",
                        "context.thread_id": "",
                        "context.work_item_id": "",
                        "context.acquired_at": "",
                        "context.updated_at": _now(),
                    }
                },
            )
            if released is None:
                continue
        claimed = await transaction.find_one_and_update(
            OBJECT_COLLECTION,
            {"id": slot_id, "context.work_item_id": ""},
            {
                "$set": {
                    "context.workspace_id": workspace_id,
                    "context.thread_id": thread.id,
                    "context.work_item_id": work_item.work_item_id,
                    "context.acquired_at": _now(),
                    "context.updated_at": _now(),
                }
            },
        )
        if claimed is not None:
            selected_slot = slot_id
            break

    if selected_slot is None:
        raise ResourceConflictError(
            message="Too many conversations are responding",
            details={
                "reason": "user_turn_limit",
                "limit": limit,
                "active": limit,
            },
        )

    # Another retry of this WorkItem has already installed the thread pointer.
    # The unique WorkItem insert serializes that retry before reaching here.
    if current_id == work_item.work_item_id:
        return

    now = _next_updated_at(thread.updated_at)
    updated = await transaction.find_one_and_update(
        NODE_COLLECTION,
        {"id": thread.id, "context.updated_at": thread.updated_at},
        {
            "$set": {
                "context.active_work_item_id": work_item.work_item_id,
                "context.updated_at": now,
            }
        },
    )
    if updated is None:
        current = await transaction.get(NODE_COLLECTION, thread.id)
        current_context = (current or {}).get("context") or {}
        if current_context.get("active_work_item_id") != work_item.work_item_id:
            raise ResourceConflictError(
                message="Turn admission lost a thread reservation race",
                details={"thread_id": thread.id, "reason": "thread_busy"},
            )
        # A concurrent retry of the same idempotent WorkItem won the CAS.
        # Adopt its pointer so append_message cannot overwrite it with a stale
        # in-memory ChatThread projection later in this transaction.
        thread.active_work_item_id = work_item.work_item_id
        thread.updated_at = str(current_context.get("updated_at") or thread.updated_at)
        return
    thread.active_work_item_id = work_item.work_item_id
    thread.updated_at = now


async def release_chat_turn_admission_in_transaction(
    *,
    transaction: Any,
    thread: ChatThread | None,
    work_item_id: str,
    thread_id: str = "",
) -> None:
    """Release exact matching slot/pointer; safe to call repeatedly."""
    # Read within the caller's transaction, including kernel-owned transactions
    # that have not rebound GraphContext. Never depend on a cached thread.
    slots = await transaction.find(
        OBJECT_COLLECTION,
        {"entity": "HarnessTurnAdmissionSlot", "context.work_item_id": work_item_id},
    )
    for slot in slots:
        await transaction.find_one_and_update(
            OBJECT_COLLECTION,
            {"id": slot["id"], "context.work_item_id": work_item_id},
            {
                "$set": {
                    "context.workspace_id": "",
                    "context.thread_id": "",
                    "context.work_item_id": "",
                    "context.acquired_at": "",
                    "context.updated_at": _now(),
                }
            },
        )

    target_thread_id = thread.id if thread is not None else thread_id
    record = (
        await transaction.get(NODE_COLLECTION, target_thread_id)
        if target_thread_id
        else None
    )
    if record is None or record.get("entity") != "ChatThread":
        return
    context = record.get("context") or {}
    if context.get("active_work_item_id") != work_item_id:
        return
    await transaction.find_one_and_update(
        NODE_COLLECTION,
        {
            "id": target_thread_id,
            "context.active_work_item_id": work_item_id,
        },
        {
            "$set": {
                "context.active_work_item_id": "",
                "context.updated_at": _next_updated_at(context.get("updated_at")),
            }
        },
    )


async def release_chat_turn_admission(work_item_id: str) -> None:
    """Release a turn only after its WorkItem has durably reached terminal state."""
    from app.services.app_operations.transaction_scope import (
        OperationTransactionUnavailable,
        graph_transaction_available,
        postgres_graph_transaction,
    )

    if not graph_transaction_available():
        raise OperationTransactionUnavailable(
            "Turn admission release requires a transactional shared store"
        )
    async with postgres_graph_transaction() as transaction:
        item = await WorkItem.get(f"o.WorkItem.{work_item_id}")
        if item is None:
            raise WorkError("work.not_found", "chat turn WorkItem not found")
        if item.status not in TERMINAL_WORK_STATUSES:
            raise WorkError(
                "work.admission_not_terminal",
                "turn admission can be released only after terminal transition",
            )
        thread = await ChatThread.get(item.thread_id) if item.thread_id else None
        await release_chat_turn_admission_in_transaction(
            transaction=transaction,
            thread=thread,
            work_item_id=work_item_id,
        )


__all__ = [
    "release_chat_turn_admission",
    "release_chat_turn_admission_in_transaction",
    "reserve_chat_turn_admission",
]
