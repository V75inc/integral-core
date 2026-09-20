"""Transactional command receipts for app-operation execution.

The receipt claim, local graph effects, and completed result share one
PostgreSQL transaction.  A failed handler rolls all of them back; a concurrent
retry waits for the winning transaction and replays its completed receipt.

This is a persistence primitive.  The dispatcher is not wired to it until its
change-event delivery path uses a committed outbox as well.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Iterable

from app.contracts.operations import OperationIdentity
from app.services.app_operations.transaction_scope import postgres_graph_transaction
from app.utils.time import utc_now_iso

_OBJECT_COLLECTION = "object"
_RECEIPT_ENTITY = "OperationExecutionReceipt"


class OperationReceiptConflict(ValueError):
    """A logical operation id was reused with a different request body."""


class OperationReceiptIncomplete(RuntimeError):
    """A legacy/incomplete receipt cannot safely be replayed."""


@dataclass(frozen=True)
class OperationExecutionResult:
    """A committed command result or an exact replay of one."""

    result: Dict[str, Any]
    replayed: bool


def receipt_object_id(identity: OperationIdentity) -> str:
    """Return the deterministic object id for one logical operation."""
    encoded = json.dumps(
        identity.cache_key(), separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return f"o.{_RECEIPT_ENTITY}.{hashlib.sha256(encoded).hexdigest()}"


def _receipt_document(
    *,
    identity: OperationIdentity,
    request_hash: str,
    status: str,
    result_json: str = "",
) -> Dict[str, Any]:
    return {
        "id": receipt_object_id(identity),
        "entity": _RECEIPT_ENTITY,
        "context": {
            "workspace_id": identity.workspace_id,
            "app_id": identity.app_id,
            "operation_key": identity.operation_key,
            "principal_id": identity.principal_id,
            "idempotency_key": identity.idempotency_key,
            "request_hash": request_hash,
            "status": status,
            "result_json": result_json,
            "updated_at": utc_now_iso(),
        },
    }


def _replay_or_raise(record: Dict[str, Any], *, request_hash: str) -> Dict[str, Any]:
    context = record.get("context") or {}
    if str(context.get("request_hash") or "") != request_hash:
        raise OperationReceiptConflict("Idempotency key reused with different payload")
    if str(context.get("status") or "") != "succeeded":
        raise OperationReceiptIncomplete(
            "Operation receipt is incomplete and cannot safely be replayed"
        )
    try:
        result = json.loads(str(context.get("result_json") or ""))
    except json.JSONDecodeError as exc:
        raise OperationReceiptIncomplete("Operation receipt result is invalid") from exc
    if not isinstance(result, dict):
        raise OperationReceiptIncomplete("Operation receipt result is not an object")
    return result


async def execute_operation_once(
    *,
    identity: OperationIdentity,
    request_hash: str,
    execute: Callable[[], Awaitable[Dict[str, Any]]],
    event_outbox: Iterable[Dict[str, Any]] | None = None,
    database: Any | None = None,
) -> OperationExecutionResult:
    """Execute a local command once and commit its receipt with graph effects.

    ``execute`` runs only after this invocation wins the deterministic receipt
    claim.  It inherits the transaction-bound default graph context, so normal
    jvspatial node and edge writes participate in the same commit.
    """
    if not request_hash:
        raise ValueError("request_hash is required")

    async with postgres_graph_transaction(database) as transaction:
        claim = _receipt_document(
            identity=identity,
            request_hash=request_hash,
            status="claimed",
        )
        inserted = await transaction.insert_if_absent(_OBJECT_COLLECTION, claim)
        if not inserted.created:
            existing = inserted.record or await transaction.get(
                _OBJECT_COLLECTION, claim["id"]
            )
            if existing is None:
                raise OperationReceiptIncomplete("Operation receipt claim disappeared")
            return OperationExecutionResult(
                result=_replay_or_raise(existing, request_hash=request_hash),
                replayed=True,
            )

        result = await execute()
        if not isinstance(result, dict):
            raise TypeError("operation execute callback must return a dict")
        if event_outbox:
            from app.services.app_operations.event_outbox import insert_operation_events

            await insert_operation_events(
                transaction=transaction, identity=identity, events=event_outbox
            )
        serialized = json.dumps(result, sort_keys=True, separators=(",", ":"))
        completed = _receipt_document(
            identity=identity,
            request_hash=request_hash,
            status="succeeded",
            result_json=serialized,
        )
        await transaction.save(_OBJECT_COLLECTION, completed)
        return OperationExecutionResult(result=dict(result), replayed=False)


__all__ = [
    "OperationExecutionResult",
    "OperationReceiptConflict",
    "OperationReceiptIncomplete",
    "execute_operation_once",
    "receipt_object_id",
]
