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
    return await insert_revision_event(
        transaction=transaction,
        workspace_id=workspace_id,
        namespace="entry-command-v1",
        subject_id=event["resource_id"],
        revision=after.get("record_revision", 1),
        event=event,
    )


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
            event["action"],
            revision,
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


_BATCH_SIZE = 100


async def _bulk_update(transaction: Any, sql_template: str, *args: Any) -> list:
    """Run one SQL statement on the transaction's own connection (``{table}`` is the object table)."""
    concrete = transaction
    seen: set[int] = set()
    while getattr(concrete, "inner", None) is not None and id(concrete) not in seen:
        seen.add(id(concrete))
        concrete = concrete.inner
    from jvspatial.db.postgres import _safe_collection

    table = (
        f"{_safe_collection(concrete._db.schema_name)}.{_safe_collection(_COLLECTION)}"
    )
    async with concrete._operation_lock:
        concrete._ensure_active()
        return await concrete._connection.fetch(sql_template.format(table=table), *args)


async def deliver_operation_events(
    outbox_ids: Iterable[str], *, database: Any | None = None
) -> int:
    """Deliver fresh pending facts in batches: one claim and one mark statement per batch.

    Same contract as ``deliver_operation_event`` (claim, emit through the normal ChangeEvent
    path, then mark delivered; a crash between emit and mark can duplicate a log row, never lose
    the event). Only never-attempted pending facts take this path. Anything else was already
    handled, or is being handled, by the writer or the recovery sweep, and is left to it.
    """
    from app.services.change_event import emit_change_event

    ids = list(dict.fromkeys(str(i) for i in outbox_ids if i))
    delivered = 0
    for offset in range(0, len(ids), _BATCH_SIZE):
        chunk = ids[offset : offset + _BATCH_SIZE]
        object_ids = [event_outbox_object_id(i) for i in chunk]
        lease_until = (
            datetime.now(timezone.utc) + timedelta(seconds=_LEASE_SECONDS)
        ).isoformat()
        async with postgres_graph_transaction(database) as transaction:
            rows = await _bulk_update(
                transaction,
                """UPDATE {table} SET updated_at = NOW(),
                       data = jsonb_set(data, '{{context}}', (data->'context') || $2::jsonb)
                   WHERE id = ANY($1::text[]) AND entity = $3
                     AND data #>> '{{context,status}}' = 'pending'
                     AND data #>> '{{context,attempt}}' = '0'
                   RETURNING id, data #>> '{{context,outbox_id}}' AS outbox_id,
                             (data #> '{{context,event}}')::text AS event""",
                object_ids,
                json.dumps(
                    {
                        "status": "delivering",
                        "lease_until": lease_until,
                        "attempt": 1,
                        "updated_at": utc_now_iso(),
                    }
                ),
                _ENTITY,
            )
        emitted: list[str] = []
        for row in rows:
            outbox_id = str(row["outbox_id"])
            event = dict(json.loads(row["event"]) if row["event"] else {})
            details = dict(event.get("details") or {})
            details["event_fact_id"] = outbox_id
            event["details"] = details
            try:
                await emit_change_event(**event)
                emitted.append(row["id"])
            except Exception as exc:  # noqa: BLE001 - leave a durable pending fact
                logger.warning(
                    "Batched event delivery failed for %s: %s", outbox_id, exc
                )
                async with postgres_graph_transaction(database) as transaction:
                    await _bulk_update(
                        transaction,
                        """UPDATE {table} SET updated_at = NOW(),
                               data = jsonb_set(data, '{{context}}', (data->'context') || $2::jsonb)
                           WHERE id = $1 RETURNING id""",
                        row["id"],
                        json.dumps(
                            {
                                "status": "pending",
                                "last_error": str(exc)[:500],
                                "updated_at": utc_now_iso(),
                            }
                        ),
                    )
        if emitted:
            async with postgres_graph_transaction(database) as transaction:
                await _bulk_update(
                    transaction,
                    """UPDATE {table} SET updated_at = NOW(),
                           data = jsonb_set(data, '{{context}}',
                                            ((data->'context') - 'lease_until') || $2::jsonb)
                       WHERE id = ANY($1::text[]) RETURNING id""",
                    emitted,
                    json.dumps({"status": "delivered", "updated_at": utc_now_iso()}),
                )
            delivered += len(emitted)
    return delivered


async def flush_deferred_job_events() -> int:
    """Deliver, in batches, the event facts an App job deferred. Never raises."""
    from app.middleware.permissions_cache import take_deferred_job_events

    pending = take_deferred_job_events()
    if not pending:
        return 0
    try:
        return await deliver_operation_events(pending)
    except Exception:
        logger.exception("Deferred job events await recovery")
        return 0


async def deliver_pending_operation_events(
    *, database: Any | None = None, min_age_seconds: float = 0.0
) -> int:
    """Best-effort recovery sweep for committed operation event facts.

    ``min_age_seconds`` leaves alone facts created more recently than that. The writer delivers its
    own event right after its commit, and a periodic sweep that claims the same fresh rows waits on
    the writer's row lock (and makes the writer wait on the sweep's); the sweep exists to recover
    facts a crash left behind, which are old by the time it looks.
    """
    from jvspatial.db import get_prime_database

    from app.services.app_operations.transaction_scope import _transaction_database

    db = _transaction_database(database or get_prime_database())
    query: Dict[str, Any] = {
        "entity": _ENTITY,
        "context.status": {"$in": ["pending", "delivering"]},
    }
    if min_age_seconds > 0:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=min_age_seconds)
        ).isoformat()
        query["context.created_at"] = {"$lte": cutoff}
    records = await db.find(_COLLECTION, query)
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
    from app.services.entry_cleanup_outbox import deliver_pending_cleanup

    await deliver_pending_cleanup(database=db)
    from app.services.bundle_cleanup_outbox import deliver_bundle_cleanup

    await deliver_bundle_cleanup(database=db)
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
