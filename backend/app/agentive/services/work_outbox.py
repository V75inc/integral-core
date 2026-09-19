"""Transactional WorkItem + outbox units and at-least-once delivery."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, Optional

from jvspatial.db import get_prime_database

from app.agentive.work_models import WorkItem, WorkOutboxEntry
from app.schemas.agentive.work import (
    LEGAL_WORK_TRANSITIONS,
    EnqueueWorkRequest,
    RetryPolicy,
    WorkError,
    WorkKind,
    WorkStatus,
)
from app.utils.time import utc_now_iso

log = logging.getLogger(__name__)

OBJECT_COLLECTION = "object"
TOPIC_ENQUEUED = "work.enqueued"
TOPIC_TRANSITIONED = "work.transitioned"

_dev_lock = asyncio.Lock()

OutboxConsumer = Callable[[WorkOutboxEntry], Awaitable[None]]


def outbox_id_for(*, work_item_id: str, topic: str, seq: int) -> str:
    """Deterministic outbox identity for one work fact."""
    canonical = f"{work_item_id}:{topic}:{int(seq)}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def outbox_object_id(outbox_id: str) -> str:
    return f"o.WorkOutboxEntry.{outbox_id}"


def build_outbox_document(
    *,
    outbox_id: str,
    work_item_id: str,
    topic: str,
    payload: Dict[str, Any],
    created_at: str,
    run_id: str = "",
    causation_id: str = "",
    status: str = "pending",
    attempt: int = 0,
    available_at: str = "",
    deadline_at: str = "",
) -> Dict[str, Any]:
    """Persistence document for ``insert_if_absent`` / Object create."""
    return {
        "id": outbox_object_id(outbox_id),
        "entity": "WorkOutboxEntry",
        "context": {
            "outbox_id": outbox_id,
            "work_item_id": work_item_id,
            "topic": topic,
            "status": status,
            "payload": dict(payload or {}),
            "run_id": run_id,
            "causation_id": causation_id,
            "attempt": attempt,
            "available_at": available_at or created_at,
            "deadline_at": deadline_at,
            "lease_owner": "",
            "lease_token": "",
            "lease_expires_at": "",
            "created_at": created_at,
            "updated_at": created_at,
        },
    }


def build_work_item_document(
    *,
    object_id: str,
    work_item_id: str,
    kind: str,
    origin: str,
    principal_id: str,
    workspace_id: str,
    idempotency_key: str,
    input_payload: Dict[str, Any],
    input_fingerprint: str,
    status: str,
    attempt: int,
    transition_seq: int,
    next_attempt_at: str,
    created_at: str,
    updated_at: str,
    retry_policy: Dict[str, Any],
    thread_id: str = "",
    app_id: str = "",
    parent_work_item_id: str = "",
    causation_id: str = "",
    deadline_at: str = "",
) -> Dict[str, Any]:
    return {
        "id": object_id,
        "entity": "WorkItem",
        "context": {
            "work_item_id": work_item_id,
            "kind": kind,
            "origin": origin,
            "principal_id": principal_id,
            "workspace_id": workspace_id,
            "thread_id": thread_id,
            "app_id": app_id,
            "parent_work_item_id": parent_work_item_id,
            "causation_id": causation_id,
            "idempotency_key": idempotency_key,
            "input_payload": dict(input_payload or {}),
            "input_fingerprint": input_fingerprint,
            "status": status,
            "attempt": attempt,
            "retry_policy": dict(retry_policy or {}),
            "next_attempt_at": next_attempt_at,
            "deadline_at": deadline_at,
            "cancel_requested_at": "",
            "lease_owner": "",
            "lease_token": "",
            "lease_fence": 0,
            "lease_expires_at": "",
            "transition_seq": transition_seq,
            "run_id": "",
            "result_refs": [],
            "result_fingerprint": "",
            "failure": None,
            "created_at": created_at,
            "updated_at": updated_at,
        },
    }


def _unwrap_database(db: Any) -> Any:
    """Walk Observable/Caching wrappers down to the concrete backend."""
    cur = db
    seen: set[int] = set()
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if type(cur).__name__ == "PostgresDB":
            return cur
        inner = getattr(cur, "inner", None)
        if inner is None:
            break
        cur = inner
    return db


def _is_postgres_txn_db(db: Any) -> bool:
    inner = _unwrap_database(db)
    return type(inner).__name__ == "PostgresDB" and bool(
        getattr(inner, "supports_transactions", False)
    ) and callable(getattr(inner, "begin_transaction", None))


def _txn_database(db: Any) -> Any:
    """Database handle that actually exposes begin/commit/rollback."""
    return _unwrap_database(db)


def _work_item_object_id(
    *,
    kind: str,
    origin: str,
    principal_id: str,
    workspace_id: str,
    idempotency_key: str,
) -> str:
    from app.agentive.services.work_items import work_item_object_id

    return work_item_object_id(
        kind=kind,
        origin=origin,
        principal_id=principal_id,
        workspace_id=workspace_id,
        idempotency_key=idempotency_key,
    )


def _input_fingerprint(payload: Dict[str, Any]) -> str:
    from app.agentive.services.work_items import input_fingerprint

    return input_fingerprint(payload)


def _hydrate_work_item(doc: Dict[str, Any]) -> WorkItem:
    ctx = dict(doc.get("context") or {})
    return WorkItem(id=doc["id"], **ctx)


def _conflict_if_mismatched(existing: WorkItem, req: EnqueueWorkRequest, fp: str) -> None:
    if (
        existing.input_fingerprint != fp
        or existing.kind != req.kind
        or existing.origin != req.origin
        or existing.principal_id != req.principal_id
        or existing.workspace_id != req.workspace_id
    ):
        raise WorkError(
            "work.idempotency_conflict",
            "idempotency key reused with different work identity or input",
        )


async def enqueue_work_item_unit(
    *,
    kind: WorkKind,
    origin: str,
    principal_id: str,
    workspace_id: str,
    idempotency_key: str,
    input_payload: Optional[Dict[str, Any]] = None,
    thread_id: Optional[str] = None,
    app_id: Optional[str] = None,
    parent_work_item_id: Optional[str] = None,
    causation_id: Optional[str] = None,
    deadline_at: Optional[str] = None,
    retry_policy: Optional[RetryPolicy] = None,
    transaction: Any = None,
) -> WorkItem:
    """Create WorkItem + initial outbox fact as one unit when possible."""
    req = EnqueueWorkRequest(
        kind=kind,
        origin=origin,
        principal_id=principal_id,
        workspace_id=workspace_id,
        idempotency_key=idempotency_key,
        input_payload=dict(input_payload or {}),
        thread_id=thread_id,
        app_id=app_id,
        parent_work_item_id=parent_work_item_id,
        causation_id=causation_id,
        deadline_at=deadline_at,
        retry_policy=retry_policy or RetryPolicy(),
    )
    object_id = _work_item_object_id(
        kind=req.kind,
        origin=req.origin,
        principal_id=req.principal_id,
        workspace_id=req.workspace_id,
        idempotency_key=req.idempotency_key,
    )
    work_item_id = object_id.removeprefix("o.WorkItem.")
    fingerprint = _input_fingerprint(req.input_payload)
    now = utc_now_iso()
    work_doc = build_work_item_document(
        object_id=object_id,
        work_item_id=work_item_id,
        kind=req.kind,
        origin=req.origin,
        principal_id=req.principal_id,
        workspace_id=req.workspace_id,
        idempotency_key=req.idempotency_key,
        input_payload=req.input_payload,
        input_fingerprint=fingerprint,
        status="queued",
        attempt=0,
        transition_seq=0,
        next_attempt_at=now,
        created_at=now,
        updated_at=now,
        retry_policy=req.retry_policy.model_dump(),
        thread_id=req.thread_id or "",
        app_id=req.app_id or "",
        parent_work_item_id=req.parent_work_item_id or "",
        causation_id=req.causation_id or "",
        deadline_at=req.deadline_at or "",
    )
    outbox_id = outbox_id_for(
        work_item_id=work_item_id, topic=TOPIC_ENQUEUED, seq=0
    )
    outbox_doc = build_outbox_document(
        outbox_id=outbox_id,
        work_item_id=work_item_id,
        topic=TOPIC_ENQUEUED,
        payload={
            "status": "queued",
            "kind": req.kind,
            "origin": req.origin,
        },
        created_at=now,
        causation_id=req.causation_id or "",
    )

    db = get_prime_database()
    if transaction is not None or _is_postgres_txn_db(db):
        return await _enqueue_postgres(
            db=_txn_database(db) if transaction is None else db,
            transaction=transaction,
            object_id=object_id,
            work_doc=work_doc,
            outbox_doc=outbox_doc,
            req=req,
            fingerprint=fingerprint,
        )
    async with _dev_lock:
        return await _enqueue_dev(
            object_id=object_id,
            work_doc=work_doc,
            outbox_doc=outbox_doc,
            req=req,
            fingerprint=fingerprint,
        )


async def _enqueue_postgres(
    *,
    db: Any,
    transaction: Any,
    object_id: str,
    work_doc: Dict[str, Any],
    outbox_doc: Dict[str, Any],
    req: EnqueueWorkRequest,
    fingerprint: str,
) -> WorkItem:
    owns_txn = transaction is None
    txn = transaction
    if owns_txn:
        txn = await db.begin_transaction()
    try:
        inserted = await txn.insert_if_absent(OBJECT_COLLECTION, work_doc)
        if not inserted.created:
            existing_doc = inserted.record or await txn.get(
                OBJECT_COLLECTION, object_id
            )
            if existing_doc is None:
                existing_doc = await db.get(OBJECT_COLLECTION, object_id)
            existing = _hydrate_work_item(existing_doc)
            _conflict_if_mismatched(existing, req, fingerprint)
            # Ensure outbox fact exists for prior create (idempotent).
            await txn.insert_if_absent(OBJECT_COLLECTION, outbox_doc)
            if owns_txn:
                await db.commit_transaction(txn)
            return existing
        await txn.insert_if_absent(OBJECT_COLLECTION, outbox_doc)
        if owns_txn:
            await db.commit_transaction(txn)
        return _hydrate_work_item(work_doc)
    except Exception:
        if owns_txn and txn is not None:
            await db.rollback_transaction(txn)
        raise


async def _enqueue_dev(
    *,
    object_id: str,
    work_doc: Dict[str, Any],
    outbox_doc: Dict[str, Any],
    req: EnqueueWorkRequest,
    fingerprint: str,
) -> WorkItem:
    ctx = work_doc["context"]
    record, created = await WorkItem.create_if_absent(id=object_id, **ctx)
    if not created:
        _conflict_if_mismatched(record, req, fingerprint)
    else:
        record = _hydrate_work_item(work_doc)
    out_ctx = outbox_doc["context"]
    await WorkOutboxEntry.create_if_absent(id=outbox_doc["id"], **out_ctx)
    return record


async def transition_work_item_unit(
    work_item_id: str,
    *,
    expected_status: WorkStatus,
    target: WorkStatus,
    fields: Optional[Dict[str, Any]] = None,
    transaction: Any = None,
) -> WorkItem:
    """Apply a legal transition + emit transition outbox fact."""
    allowed = LEGAL_WORK_TRANSITIONS.get(expected_status, frozenset())
    if target not in allowed:
        raise WorkError(
            "work.invalid_transition",
            f"cannot transition {expected_status} -> {target}",
        )

    object_id = (
        work_item_id
        if work_item_id.startswith("o.WorkItem.")
        else f"o.WorkItem.{work_item_id}"
    )
    bare_id = object_id.removeprefix("o.WorkItem.")
    now = utc_now_iso()
    field_updates = dict(fields or {})

    db = get_prime_database()
    if transaction is not None or _is_postgres_txn_db(db):
        return await _transition_postgres(
            db=_txn_database(db) if transaction is None else db,
            transaction=transaction,
            object_id=object_id,
            bare_id=bare_id,
            expected_status=expected_status,
            target=target,
            field_updates=field_updates,
            now=now,
        )
    async with _dev_lock:
        return await _transition_dev(
            object_id=object_id,
            bare_id=bare_id,
            expected_status=expected_status,
            target=target,
            field_updates=field_updates,
            now=now,
        )


async def _transition_postgres(
    *,
    db: Any,
    transaction: Any,
    object_id: str,
    bare_id: str,
    expected_status: WorkStatus,
    target: WorkStatus,
    field_updates: Dict[str, Any],
    now: str,
) -> WorkItem:
    owns_txn = transaction is None
    txn = transaction
    if owns_txn:
        txn = await db.begin_transaction()
    try:
        current = await txn.get(OBJECT_COLLECTION, object_id)
        if current is None:
            raise WorkError("work.not_found", f"work item {bare_id} not found")
        ctx = dict(current.get("context") or {})
        if ctx.get("status") != expected_status:
            raise WorkError(
                "work.invalid_transition",
                f"expected status {expected_status}, found {ctx.get('status')}",
            )
        next_seq = int(ctx.get("transition_seq") or 0) + 1
        set_fields: Dict[str, Any] = {
            "context.status": target,
            "context.transition_seq": next_seq,
            "context.updated_at": now,
        }
        for key, value in field_updates.items():
            set_fields[f"context.{key}"] = value
        updated = await txn.find_one_and_update(
            OBJECT_COLLECTION,
            {"id": object_id, "context.status": expected_status},
            {"$set": set_fields},
        )
        if updated is None:
            raise WorkError(
                "work.invalid_transition",
                f"expected status {expected_status}, found concurrent change",
            )
        outbox_id = outbox_id_for(
            work_item_id=bare_id, topic=TOPIC_TRANSITIONED, seq=next_seq
        )
        await txn.insert_if_absent(
            OBJECT_COLLECTION,
            build_outbox_document(
                outbox_id=outbox_id,
                work_item_id=bare_id,
                topic=TOPIC_TRANSITIONED,
                payload={
                    "from_status": expected_status,
                    "to_status": target,
                    "transition_seq": next_seq,
                },
                created_at=now,
                run_id=str(field_updates.get("run_id") or ctx.get("run_id") or ""),
            ),
        )
        if owns_txn:
            await db.commit_transaction(txn)
        return _hydrate_work_item(updated)
    except Exception:
        if owns_txn and txn is not None:
            await db.rollback_transaction(txn)
        raise


async def _transition_dev(
    *,
    object_id: str,
    bare_id: str,
    expected_status: WorkStatus,
    target: WorkStatus,
    field_updates: Dict[str, Any],
    now: str,
) -> WorkItem:
    item = await WorkItem.get(object_id)
    if item is None:
        raise WorkError("work.not_found", f"work item {bare_id} not found")
    if item.status != expected_status:
        raise WorkError(
            "work.invalid_transition",
            f"expected status {expected_status}, found {item.status}",
        )
    next_seq = int(item.transition_seq or 0) + 1
    item.status = target
    item.transition_seq = next_seq
    item.updated_at = now
    for key, value in field_updates.items():
        setattr(item, key, value)
    await item.save()
    outbox_id = outbox_id_for(
        work_item_id=bare_id, topic=TOPIC_TRANSITIONED, seq=next_seq
    )
    await WorkOutboxEntry.create_if_absent(
        id=outbox_object_id(outbox_id),
        outbox_id=outbox_id,
        work_item_id=bare_id,
        topic=TOPIC_TRANSITIONED,
        status="pending",
        payload={
            "from_status": expected_status,
            "to_status": target,
            "transition_seq": next_seq,
        },
        run_id=str(getattr(item, "run_id", "") or ""),
        attempt=0,
        available_at=now,
        created_at=now,
        updated_at=now,
    )
    return item


async def deliver_outbox_entry(
    entry: WorkOutboxEntry,
    consumer: OutboxConsumer,
) -> bool:
    """Deliver once. Returns True when newly delivered, False if already done."""
    object_id = entry.id or outbox_object_id(entry.outbox_id)
    current = await WorkOutboxEntry.get(object_id)
    if current is None:
        raise WorkError("work.outbox_not_found", f"outbox {entry.outbox_id} missing")
    if current.status == "delivered":
        return False

    available = (current.available_at or "").strip()
    if available:
        try:
            avail_dt = datetime.fromisoformat(available.replace("Z", "+00:00"))
            if avail_dt > datetime.now(timezone.utc):
                return False
        except ValueError:
            pass

    try:
        await consumer(current)
    except Exception:
        now = utc_now_iso()
        attempt = int(current.attempt or 0) + 1
        delay = min(300, 2 ** min(attempt, 8))
        nxt = datetime.now(timezone.utc) + timedelta(seconds=delay)
        current.status = "pending"
        current.attempt = attempt
        current.available_at = nxt.isoformat()
        current.updated_at = now
        await current.save()
        return False

    now = utc_now_iso()
    current.status = "delivered"
    current.updated_at = now
    current.available_at = now
    await current.save()
    return True


async def reconcile_missing_outbox_facts(
    work_item: Optional[WorkItem] = None,
) -> int:
    """Backfill deterministic missing outbox facts (dev / recovery)."""
    items: list[WorkItem]
    if work_item is not None:
        items = [work_item]
    else:
        items = list(await WorkItem.find({}))

    created = 0
    now = utc_now_iso()
    for item in items:
        wid = item.work_item_id or item.id.removeprefix("o.WorkItem.")
        # Initial enqueue fact always expected.
        oid = outbox_id_for(work_item_id=wid, topic=TOPIC_ENQUEUED, seq=0)
        _, was_created = await WorkOutboxEntry.create_if_absent(
            id=outbox_object_id(oid),
            outbox_id=oid,
            work_item_id=wid,
            topic=TOPIC_ENQUEUED,
            status="pending",
            payload={"status": item.status, "kind": item.kind, "origin": item.origin},
            attempt=0,
            available_at=now,
            created_at=now,
            updated_at=now,
        )
        if was_created:
            created += 1
        seq = int(item.transition_seq or 0)
        if seq > 0:
            tid = outbox_id_for(
                work_item_id=wid, topic=TOPIC_TRANSITIONED, seq=seq
            )
            _, t_created = await WorkOutboxEntry.create_if_absent(
                id=outbox_object_id(tid),
                outbox_id=tid,
                work_item_id=wid,
                topic=TOPIC_TRANSITIONED,
                status="pending",
                payload={
                    "to_status": item.status,
                    "transition_seq": seq,
                },
                attempt=0,
                available_at=now,
                created_at=now,
                updated_at=now,
            )
            if t_created:
                created += 1
    return created


__all__ = [
    "TOPIC_ENQUEUED",
    "TOPIC_TRANSITIONED",
    "build_outbox_document",
    "build_work_item_document",
    "deliver_outbox_entry",
    "enqueue_work_item_unit",
    "outbox_id_for",
    "outbox_object_id",
    "reconcile_missing_outbox_facts",
    "transition_work_item_unit",
]
