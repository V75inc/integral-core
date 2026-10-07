"""Durable, post-commit ChangeEvent delivery for app-operation commands.

The primary graph and the logging database cannot share a transaction.  A
command therefore records a deterministic event fact with its receipt and
graph effects, then a consumer emits the normal ChangeEvent only after that
transaction commits.  Delivery is at-least-once: consumers must use the
outbox id as their stable correlation key when deduplication matters.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable

from app.contracts.operations import OperationIdentity
from app.services.app_operations.transaction_scope import postgres_graph_transaction
from app.utils.time import utc_now_iso

_COLLECTION = "object"
_ENTITY = "OperationEventOutbox"
_LEASE_SECONDS = 60
_MAX_DELIVERY_ATTEMPTS = 5
logger = logging.getLogger(__name__)


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
            "event": dict(event),
            "attempt": 0,
            "created_at": now,
            "updated_at": now,
        },
    }


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
    identity = json.dumps(
        [
            "entry-command-v1",
            workspace_id,
            event["resource_id"],
            event["action"],
            after.get("record_revision", 1),
        ],
        separators=(",", ":"),
    )
    outbox_id = hashlib.sha256(identity.encode()).hexdigest()
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
                "event": dict(event),
                "attempt": 0,
                "created_at": now,
                "updated_at": now,
            },
        },
    )
    return outbox_id


async def deliver_operation_event(
    *, outbox_id: str, database: Any | None = None
) -> bool:
    """Emit one committed fact through the normal ChangeEvent path.

    The status transition is deliberately after emission.  A process crash in
    that interval can duplicate a log row, but can never lose the event.
    """
    object_id = event_outbox_object_id(outbox_id)
    lease_until = (
        datetime.now(timezone.utc) + timedelta(seconds=_LEASE_SECONDS)
    ).isoformat()
    async with postgres_graph_transaction(database) as transaction:
        record = await transaction.get(_COLLECTION, object_id)
        if record is None:
            return False
        context = dict(record.get("context") or {})
        observed = context.get("provider_result")
        status = str(context.get("status") or "unknown")
        if status in {"unknown", "in_flight"} and not isinstance(observed, dict):
            return False
        if (
            status == "delivering"
            and str(context.get("lease_until") or "") > utc_now_iso()
        ):
            return False
        if status not in {"pending", "delivering", "unknown", "in_flight"}:
            return False
        attempts = int(context.get("attempt") or 0)
        if attempts >= _MAX_DELIVERY_ATTEMPTS:
            context.update({"status": "dead_letter", "updated_at": utc_now_iso()})
            record["context"] = context
            await transaction.save(_COLLECTION, record)
            return False
        # Atomically claim pending work. The row lock is retained through the
        # transaction commit, so overlapping sweep workers cannot both emit.
        claim_query = {"id": object_id, "context.status": status}
        if status == "delivering":
            claim_query["context.lease_until"] = {"$lte": utc_now_iso()}
        claimed = await transaction.find_one_and_update(
            _COLLECTION,
            claim_query,
            {
                "$set": {
                    "context.status": "delivering",
                    "context.lease_until": lease_until,
                    "context.attempt": attempts + 1,
                    "context.updated_at": utc_now_iso(),
                }
            },
        )
        if claimed is None:
            return False
        decision = reconcile_external_outcome(
            status=status,
            correlation_id=str(context.get("outbox_id") or outbox_id),
            observed_result=observed if isinstance(observed, dict) else None,
        )
        if decision["action"] == "skip":
            return False
        if decision["action"] == "reconcile":
            return False
        if decision["action"] == "apply_observed":
            context.update({"status": "delivered", "updated_at": utc_now_iso()})
            record["context"] = context
            await transaction.save(_COLLECTION, record)
            return True
        event = dict(context.get("event") or {})
        # Marking delivery is intentionally outside the graph-write command
        # transaction; this consumer runs only after its commit.
    from app.services.change_event import emit_change_event

    details = dict(event.get("details") or {})
    details["event_fact_id"] = outbox_id
    event["details"] = details
    try:
        await emit_change_event(**event)
    except Exception as exc:
        async with postgres_graph_transaction(database) as transaction:
            record = await transaction.get(_COLLECTION, object_id)
            if record is not None:
                context = dict(record.get("context") or {})
                context.update(
                    {
                        "status": (
                            "pending"
                            if int(context.get("attempt") or 0) < _MAX_DELIVERY_ATTEMPTS
                            else "dead_letter"
                        ),
                        "last_error": str(exc)[:500],
                        "updated_at": utc_now_iso(),
                    }
                )
                record["context"] = context
                await transaction.save(_COLLECTION, record)
        raise
    async with postgres_graph_transaction(database) as transaction:
        record = await transaction.get(_COLLECTION, object_id)
        if record is None:
            return False
        context = dict(record.get("context") or {})
        if context.get("status") == "delivered":
            return False
        context.update({"status": "delivered", "updated_at": utc_now_iso()})
        context.pop("lease_until", None)
        record["context"] = context
        await transaction.save(_COLLECTION, record)
    return True


async def deliver_pending_operation_events(*, database: Any | None = None) -> int:
    """Best-effort recovery sweep for committed operation event facts."""
    from jvspatial.db import get_prime_database

    from app.services.app_operations.transaction_scope import _transaction_database

    db = _transaction_database(database or get_prime_database())
    records = await db.find(
        _COLLECTION,
        {"entity": _ENTITY, "context.status": {"$in": ["pending", "delivering"]}},
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
