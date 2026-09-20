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
from typing import Any, Dict, Iterable

from app.contracts.operations import OperationIdentity
from app.services.app_operations.transaction_scope import postgres_graph_transaction
from app.utils.time import utc_now_iso

_COLLECTION = "object"
_ENTITY = "OperationEventOutbox"


def event_outbox_id(identity: OperationIdentity, sequence: int) -> str:
    """Return a deterministic id for one committed operation event."""
    raw = json.dumps(
        [*identity.cache_key(), int(sequence)], separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def event_outbox_object_id(outbox_id: str) -> str:
    """Return the persisted object id for an operation event fact."""
    return f"o.{_ENTITY}.{outbox_id}"


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


async def deliver_operation_event(
    *, outbox_id: str, database: Any | None = None
) -> bool:
    """Emit one committed fact through the normal ChangeEvent path.

    The status transition is deliberately after emission.  A process crash in
    that interval can duplicate a log row, but can never lose the event.
    """
    object_id = event_outbox_object_id(outbox_id)
    async with postgres_graph_transaction(database) as transaction:
        record = await transaction.get(_COLLECTION, object_id)
        if record is None:
            return False
        context = dict(record.get("context") or {})
        if context.get("status") == "delivered":
            return False
        event = dict(context.get("event") or {})
        # Marking delivery is intentionally outside the graph-write command
        # transaction; this consumer runs only after its commit.
    from app.services.change_event import emit_change_event

    await emit_change_event(**event)
    async with postgres_graph_transaction(database) as transaction:
        record = await transaction.get(_COLLECTION, object_id)
        if record is None:
            return False
        context = dict(record.get("context") or {})
        if context.get("status") == "delivered":
            return False
        context.update({"status": "delivered", "updated_at": utc_now_iso()})
        record["context"] = context
        await transaction.save(_COLLECTION, record)
    return True


async def deliver_pending_operation_events(*, database: Any | None = None) -> int:
    """Best-effort recovery sweep for committed operation event facts."""
    from jvspatial.db import get_prime_database

    from app.services.app_operations.transaction_scope import _transaction_database

    db = _transaction_database(database or get_prime_database())
    records = await db.find(
        _COLLECTION, {"entity": _ENTITY, "context.status": "pending"}
    )
    delivered = 0
    for record in records:
        ctx = dict(record.get("context") or {})
        if await deliver_operation_event(
            outbox_id=str(ctx.get("outbox_id") or ""), database=db
        ):
            delivered += 1
    return delivered


__all__ = [
    "build_event_outbox_document",
    "deliver_operation_event",
    "deliver_pending_operation_events",
    "event_outbox_id",
    "event_outbox_object_id",
    "insert_operation_events",
]
