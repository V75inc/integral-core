"""Durable, post-commit ChangeEvent delivery for app-operation commands.

The primary graph and the logging database cannot share a transaction.  A
command therefore records a deterministic event fact with its receipt and
graph effects, then a consumer emits the normal ChangeEvent only after that
transaction commits.  Delivery is at-least-once: consumers must use the
outbox id as their stable correlation key when deduplication matters.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import Any, Dict, Iterable

from app.contracts.operations import OperationIdentity
from app.contracts.task_semantics import RESIDENT_ASSIGNMENT_CHANGED
from app.services.app_operations.transaction_scope import postgres_graph_transaction
from app.services.perf_trace import perf_traced
from app.services.performance import capture_trace_context
from app.utils.time import utc_now_iso

_COLLECTION = "object"
_ENTITY = "OperationEventOutbox"
_LEASE_SECONDS = 60
_MAX_DELIVERY_ATTEMPTS = 5
logger = logging.getLogger(__name__)


def capture_event(event: dict) -> dict:
    """Capture provenance before request-local bindings disappear."""
    from app.services.mutation_provenance import provenance_details

    captured = dict(event)
    details = {**provenance_details(), **dict(event.get("details") or {})}
    if details:
        captured["details"] = details
    return captured


def event_outbox_id(identity: OperationIdentity, sequence: int) -> str:
    """Return a deterministic id for one committed operation event."""
    raw = json.dumps(
        [*identity.cache_key(), int(sequence)], separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def event_outbox_object_id(outbox_id: str) -> str:
    """Return the persisted object id for an operation event fact."""
    return f"o.{_ENTITY}.{outbox_id}"


def reconcile_external_outcome(
    *,
    status: str,
    correlation_id: str,
    observed_result: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Decide a retry without issuing a second external effect.

    ``unknown`` and ``in_flight`` wait for the correlated observation.
    An observed unknown is recorded once. ``pending`` still delivers.
    A delivered result is not retried.
    """
    normalized = (status or "unknown").strip().lower() or "unknown"
    if normalized in {"delivered", "succeeded"}:
        return {
            "action": "skip",
            "correlation_id": correlation_id,
            "retry": False,
        }
    if observed_result is not None and normalized in {"unknown", "in_flight"}:
        return {
            "action": "apply_observed",
            "correlation_id": correlation_id,
            "retry": False,
        }
    if normalized in {"unknown", "in_flight"}:
        return {
            "action": "reconcile",
            "correlation_id": correlation_id,
            "retry": False,
        }
    return {"action": "deliver", "correlation_id": correlation_id, "retry": False}


def build_event_outbox_document(
    *, identity: OperationIdentity, sequence: int, event: Dict[str, Any]
) -> Dict[str, Any]:
    """Build the raw record inserted alongside the execution receipt."""
    outbox_id = event_outbox_id(identity, sequence)
    now = utc_now_iso()
    return {
        "id": event_outbox_object_id(outbox_id),
        "entity": _ENTITY,
        "context": {
            "outbox_id": outbox_id,
            "workspace_id": identity.workspace_id,
            "app_id": identity.app_id,
            "operation_key": identity.operation_key,
            "status": "pending",
            "event": capture_event(event),
            "traceparent": capture_trace_context(),
            "attempt": 0,
            "created_at": now,
            "updated_at": now,
        },
    }


@perf_traced("save.event_enqueue")
async def insert_operation_events(
    *, transaction: Any, identity: OperationIdentity, events: Iterable[Dict[str, Any]]
) -> None:
    """Insert event facts in the caller's already-open command transaction."""
    for sequence, event in enumerate(events):
        await transaction.insert_if_absent(
            _COLLECTION,
            build_event_outbox_document(
                identity=identity, sequence=sequence, event=dict(event)
            ),
        )


async def insert_entry_event(
    *, transaction: Any, workspace_id: str, event: Dict[str, Any]
) -> str:
    """Reuse the command outbox for one revision-bound substrate entry fact.

    No App operation is invented for generic CRUD. The same delivery/recovery
    consumer handles this fact after the graph transaction commits.
    """
    after = dict(event.get("after") or {})
    return await insert_revision_event(
        transaction=transaction,
        workspace_id=workspace_id,
        namespace="entry-command-v1",
        subject_id=event["resource_id"],
        revision=after.get("record_revision", 1),
        event=event,
    )


def revision_event_fact_id(
    *,
    workspace_id: str,
    namespace: str,
    subject_id: str,
    revision: int,
    action: str,
) -> str:
    """Identify a lifecycle fact independently of delivery attempts."""
    if (
        not namespace
        or not workspace_id
        or not subject_id
        or type(revision) is not int
        or revision < 0
    ):
        raise ValueError("Invalid lifecycle event identity")
    identity = json.dumps(
        [
            namespace,
            workspace_id,
            subject_id,
            action,
            revision,
        ],
        separators=(",", ":"),
    )
    return hashlib.sha256(identity.encode()).hexdigest()


@perf_traced("save.event_enqueue")
async def insert_revision_event(
    *,
    transaction: Any,
    workspace_id: str,
    namespace: str,
    subject_id: str,
    revision: int,
    event: Dict[str, Any],
) -> str:
    """Persist a namespaced lifecycle fact with its actual subject and revision."""
    outbox_id = revision_event_fact_id(
        workspace_id=workspace_id,
        namespace=namespace,
        subject_id=subject_id,
        revision=revision,
        action=event["action"],
    )
    now = utc_now_iso()
    await transaction.insert_if_absent(
        _COLLECTION,
        {
            "id": event_outbox_object_id(outbox_id),
            "entity": _ENTITY,
            "context": {
                "outbox_id": outbox_id,
                "workspace_id": workspace_id,
                "app_id": "",
                "operation_key": event["action"],
                "status": "pending",
                "event": capture_event(event),
                "traceparent": capture_trace_context(),
                "attempt": 0,
                "created_at": now,
                "updated_at": now,
            },
        },
    )
    return outbox_id


@perf_traced("event.delivery")
async def deliver_operation_event(
    *, outbox_id: str, database: Any | None = None
) -> bool:
    """Worker-only, fenced delivery. Audit persistence is independently idempotent."""
    object_id = event_outbox_object_id(outbox_id)
    owner = uuid.uuid4().hex
    now = utc_now_iso()
    async with postgres_graph_transaction(database) as transaction:
        record = await transaction.get(_COLLECTION, object_id)
        if record is None:
            return False
        context = dict(record.get("context") or {})
        status = context.get("status")
        if status in {"unknown", "in_flight"} and isinstance(
            context.get("provider_result"), dict
        ):
            observed = await transaction.find_one_and_update(
                _COLLECTION,
                {"id": object_id, "context.status": status},
                {"$set": {"context.status": "delivered", "context.updated_at": now}},
            )
            return observed is not None
        if status not in {"pending", "delivering"}:
            return False
        if status == "delivering" and context.get("lease_until", "") > now:
            return False
        if context.get("next_attempt_at", "") > now:
            return False
        attempt = int(context.get("attempt") or 0) + 1
        if attempt > _MAX_DELIVERY_ATTEMPTS:
            await transaction.find_one_and_update(
                _COLLECTION,
                {"id": object_id},
                {"$set": {"context.status": "dead_letter", "context.updated_at": now}},
            )
            return False
        query = {"id": object_id, "context.status": status}
        if status == "delivering":
            query["context.lease_until"] = {"$lte": now}
        claimed = await transaction.find_one_and_update(
            _COLLECTION,
            query,
            {
                "$set": {
                    "context.status": "delivering",
                    "context.lease_owner": owner,
                    "context.lease_until": (
                        datetime.now(timezone.utc) + timedelta(seconds=_LEASE_SECONDS)
                    ).isoformat(),
                    "context.attempt": attempt,
                    "context.updated_at": now,
                }
            },
        )
        if claimed is None:
            return False
        claimed_event = dict(context.get("event") or {})
        if claimed_event.get("action") == RESIDENT_ASSIGNMENT_CHANGED:
            # The resident-assignment intake commits with the claim, so it is recorded once even if
            # the audit write below is retried.
            from app.services.resident_assignment import (
                record_resident_assignment_intake,
            )

            await record_resident_assignment_intake(
                transaction=transaction,
                fact_id=outbox_id,
                workspace_id=str(context.get("workspace_id") or ""),
                event=claimed_event,
            )
    fence = {
        "id": object_id,
        "context.status": "delivering",
        "context.lease_owner": owner,
    }

    async def renew():
        while True:
            await asyncio.sleep(_LEASE_SECONDS / 3)
            async with postgres_graph_transaction(database) as transaction:
                renewed = await transaction.find_one_and_update(
                    _COLLECTION,
                    fence,
                    {
                        "$set": {
                            "context.lease_until": (
                                datetime.now(timezone.utc)
                                + timedelta(seconds=_LEASE_SECONDS)
                            ).isoformat()
                        }
                    },
                )
                if renewed is None:
                    raise RuntimeError("Event delivery lease lost")

    async def persist():
        from app.services.change_event import emit_change_event

        event = dict(context.get("event") or {})
        details = dict(event.get("details") or {})
        details["event_fact_id"] = outbox_id
        event["details"] = details
        event["_occurred_at"] = context.get("occurred_at") or context.get("created_at")
        workspace_id = context.get("workspace_id")
        if workspace_id and workspace_id != "unattributed":
            event["_workspace_id"] = workspace_id
        from app.services.performance import phase, worker_trace

        with worker_trace(context.get("traceparent")), phase("event.persist"):
            return await emit_change_event(**event, _persist_only=True)

    started = perf_counter()
    delivery = asyncio.create_task(persist())
    renewal = asyncio.create_task(renew())
    try:
        done, _ = await asyncio.wait(
            {delivery, renewal}, return_when=asyncio.FIRST_COMPLETED
        )
        if renewal in done:
            await renewal
        await delivery
        async with postgres_graph_transaction(database) as transaction:
            result = await transaction.find_one_and_update(
                _COLLECTION,
                fence,
                {
                    "$set": {
                        "context.status": "delivered",
                        "context.updated_at": utc_now_iso(),
                        "context.audit_completed_at": utc_now_iso(),
                        "context.delivery_ms": round(
                            (perf_counter() - started) * 1000, 3
                        ),
                    }
                },
            )
        return result is not None
    except Exception as exc:
        async with postgres_graph_transaction(database) as transaction:
            await transaction.find_one_and_update(
                _COLLECTION,
                fence,
                {
                    "$set": {
                        "context.status": (
                            "dead_letter"
                            if attempt >= _MAX_DELIVERY_ATTEMPTS
                            else "pending"
                        ),
                        "context.last_error_type": type(exc).__name__,
                        "context.next_attempt_at": (
                            datetime.now(timezone.utc)
                            + timedelta(seconds=min(60, 2**attempt))
                        ).isoformat(),
                        "context.updated_at": utc_now_iso(),
                    }
                },
            )
        raise
    finally:
        for task in (delivery, renewal):
            if not task.done():
                task.cancel()
        await asyncio.gather(delivery, renewal, return_exceptions=True)


async def capture_transaction_event(event: dict) -> bool:
    """Capture direct emissions made inside a graph write before its commit."""
    from jvspatial.core.context import get_default_context

    from app.services.entry_write_scope import is_postgres_entry_transaction

    graph = get_default_context()
    # Refusals are forensic facts independent of a rejected transaction.
    # They retain the existing separate logging-store persistence path.
    if event.get("action") == "policy.deny":
        return False
    if not is_postgres_entry_transaction(graph.database):
        return False
    from app.services.event_history import event_workspace_id

    scope = event.get("scope") or ""
    workspace_id = await event_workspace_id(scope)
    # Scope is descriptive here, never mutation authority. The original actor
    # and snapshot survive deletion; read-time authorization remains canonical.
    await insert_revision_event(
        transaction=graph.database,
        workspace_id=workspace_id or "unattributed",
        namespace="event-" + uuid.uuid4().hex,
        subject_id=event.get("resource_id") or uuid.uuid4().hex,
        revision=0,
        event=event,
    )
    return True


async def request_event_delivery(*, outbox_id: str, **kwargs) -> bool:
    """Compatibility handoff: the committed fact is already queued for the worker."""
    return False


async def request_event_deliveries(outbox_ids, **kwargs) -> int:
    """Committed batches need no API-side delivery or additional queue writes."""
    return 0


_BATCH_SIZE = 100


async def deliver_operation_events(outbox_ids: Iterable[str], *, database=None) -> int:
    """Bounded worker batch; isolate poison facts and preserve per-event fencing."""
    delivered = 0
    for outbox_id in list(dict.fromkeys(outbox_ids))[:_BATCH_SIZE]:
        try:
            delivered += await deliver_operation_event(
                outbox_id=outbox_id, database=database
            )
        except Exception:
            logger.warning(
                "Event delivery failed; durable retry retained", exc_info=False
            )
    return delivered


async def flush_deferred_job_events() -> int:
    """Forget request-local hints; facts are already committed for the worker."""
    from app.middleware.permissions_cache import take_deferred_job_events

    take_deferred_job_events()
    return 0


async def deliver_pending_operation_events(
    *, database: Any | None = None, min_age_seconds: float = 0.0
) -> int:
    """Deliver one bounded page of due committed facts in the independent worker.

    The optional minimum age remains compatible with older maintenance callers;
    normal delivery includes fresh facts. Active leases and backoff are filtered
    before the limit so they cannot starve ready records.
    """
    from jvspatial.db import get_prime_database

    from app.services.app_operations.transaction_scope import _transaction_database

    db = _transaction_database(database or get_prime_database())
    query: Dict[str, Any] = {
        "entity": _ENTITY,
        "context.status": {"$in": ["pending", "delivering"]},
    }
    now = utc_now_iso()
    query["$and"] = [
        {
            "$or": [
                {"context.next_attempt_at": {"$exists": False}},
                {"context.next_attempt_at": {"$lte": now}},
            ]
        },
        {
            "$or": [
                {"context.status": "pending"},
                {"context.lease_until": {"$lte": now}},
            ]
        },
    ]
    if min_age_seconds > 0:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=min_age_seconds)
        ).isoformat()
        query["context.created_at"] = {"$lte": cutoff}
    records = await db.find(
        _COLLECTION,
        query,
        limit=_BATCH_SIZE,
        sort=[("context.updated_at", 1), ("id", 1)],
    )
    delivered = 0
    for record in records:
        ctx = dict(record.get("context") or {})
        try:
            if await deliver_operation_event(
                outbox_id=str(ctx.get("outbox_id") or ""), database=db
            ):
                delivered += 1
        except Exception:  # isolate poison records so later facts still progress
            logger.exception(
                "operation event delivery failed (id=%s)", ctx.get("outbox_id")
            )
    return delivered


__all__ = [
    "build_event_outbox_document",
    "deliver_operation_event",
    "deliver_pending_operation_events",
    "event_outbox_id",
    "event_outbox_object_id",
    "insert_operation_events",
    "reconcile_external_outcome",
]


async def deliver_pending_cleanup_events() -> None:
    """Cleanup retains API recovery ownership independently of audit delivery."""
    from app.services.bundle_cleanup_outbox import deliver_bundle_cleanup
    from app.services.entry_cleanup_outbox import deliver_pending_cleanup

    await deliver_pending_cleanup()
    await deliver_bundle_cleanup()
