"""Transactional command receipts for app-operation execution.

The receipt claim, local graph effects, and completed result share one
PostgreSQL transaction.  A failed handler rolls all of them back; a concurrent
retry waits for the winning transaction and replays its completed receipt.

The dispatcher uses this primitive with a committed change-event outbox.
Result disclosure still requires current authorization; a completed receipt
proves the earlier effect and never grants permission to execute it again.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import nullcontext
from dataclasses import dataclass, replace
from typing import Any, AsyncContextManager, Awaitable, Callable, Dict, Iterable

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
    identity: OperationIdentity | None = None


def receipt_object_id(identity: OperationIdentity) -> str:
    """Return the deterministic object id for one logical operation."""
    encoded = json.dumps(
        identity.cache_key(), separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return f"o.{_RECEIPT_ENTITY}.{hashlib.sha256(encoded).hexdigest()}"


def receipt_reference(identity: OperationIdentity, *, replayed: bool) -> Dict[str, Any]:
    """Return the public, non-secret reference for a completed command.

    The deterministic receipt id is the logical-operation identity that
    survives HTTP, resident, MCP, and worker transport boundaries. The
    idempotency key and request fingerprint deliberately remain private.
    """
    return {
        "id": receipt_object_id(identity),
        "status": "succeeded",
        "replayed": bool(replayed),
    }


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
    principal_aliases: Iterable[str] = (),
    admit_identity: Callable[[], AsyncContextManager[None]] | None = None,
) -> OperationExecutionResult:
    """Execute a local command once and commit its receipt with graph effects.

    ``execute`` runs only after this invocation wins the deterministic receipt
    claim.  It inherits the transaction-bound default graph context, so normal
    jvspatial node and edge writes participate in the same commit.
    """
    if not request_hash:
        raise ValueError("request_hash is required")

    aliases = tuple(principal_aliases)
    if len(aliases) > 1 or any(
        type(alias) is not str or not alias or alias != alias.strip()
        for alias in aliases
    ):
        raise ValueError("operation aliases require at most one verified principal")
    identities = {receipt_object_id(identity): identity}
    for alias in aliases:
        other = replace(identity, principal_id=alias)
        identities[receipt_object_id(other)] = other

    async with postgres_graph_transaction(database) as transaction:
        created: set[str] = set()
        existing: dict[str, dict[str, Any]] = {}
        # Every current alias uses the original receipt namespace. This also
        # serializes with an older worker which knows only one of those IDs.
        for receipt_id, candidate in sorted(identities.items()):
            claim = _receipt_document(
                identity=candidate, request_hash=request_hash, status="claimed"
            )
            inserted = await transaction.insert_if_absent(_OBJECT_COLLECTION, claim)
            if inserted.created:
                created.add(receipt_id)
            else:
                record = inserted.record or await transaction.get(
                    _OBJECT_COLLECTION, receipt_id
                )
                if record is None:
                    raise OperationReceiptIncomplete(
                        "Operation receipt claim disappeared"
                    )
                _validate_receipt_identity(record, candidate)
                if record["context"].get("request_hash") != request_hash:
                    raise OperationReceiptConflict(
                        "Idempotency key reused with different payload"
                    )
                existing[receipt_id] = record

        async with admit_identity() if admit_identity is not None else nullcontext():
            completed = [
                key
                for key, row in existing.items()
                if row["context"].get("status") == "succeeded"
            ]
            if len(completed) > 1:
                raise OperationReceiptIncomplete(
                    "Multiple committed principal-alias receipts require reconciliation"
                )
            if existing and not completed:
                raise OperationReceiptIncomplete(
                    "Operation alias receipt is incomplete"
                )
            source_id = completed[0] if completed else receipt_object_id(identity)
            source_identity = identities[source_id]
            for receipt_id, row in existing.items():
                if receipt_id == source_id:
                    continue
                context = row["context"]
                if (
                    context.get("status") != "alias"
                    or context.get("canonical_receipt_id") != source_id
                    or context.get("result_json") != ""
                ):
                    raise OperationReceiptIncomplete(
                        "Operation alias receipt requires reconciliation"
                    )

            if completed:
                result = _replay_or_raise(
                    existing[source_id], request_hash=request_hash
                )
            else:
                result = await execute()
                if not isinstance(result, dict):
                    raise TypeError("operation execute callback must return a dict")
                if event_outbox:
                    from app.services.app_operations.event_outbox import (
                        insert_operation_events,
                    )

                    await insert_operation_events(
                        transaction=transaction,
                        identity=source_identity,
                        events=event_outbox,
                    )
                serialized = json.dumps(result, sort_keys=True, separators=(",", ":"))
                await transaction.save(
                    _OBJECT_COLLECTION,
                    _receipt_document(
                        identity=source_identity,
                        request_hash=request_hash,
                        status="succeeded",
                        result_json=serialized,
                    ),
                )
            for receipt_id in sorted(created - {source_id}):
                marker = _receipt_document(
                    identity=identities[receipt_id],
                    request_hash=request_hash,
                    status="alias",
                )
                marker["context"]["canonical_receipt_id"] = source_id
                await transaction.save(_OBJECT_COLLECTION, marker)
            return OperationExecutionResult(
                result=dict(result),
                replayed=bool(completed),
                identity=source_identity,
            )


def _validate_receipt_identity(record: dict, identity: OperationIdentity) -> None:
    expected = _receipt_document(identity=identity, request_hash="", status="")
    context = record.get("context")
    if (
        record.get("id") != expected["id"]
        or record.get("entity") != _RECEIPT_ENTITY
        or not isinstance(context, dict)
        or any(
            context.get(key) != expected["context"][key]
            for key in (
                "workspace_id",
                "app_id",
                "operation_key",
                "principal_id",
                "idempotency_key",
            )
        )
    ):
        raise OperationReceiptIncomplete("Operation receipt identity is invalid")


__all__ = [
    "OperationExecutionResult",
    "OperationReceiptConflict",
    "OperationReceiptIncomplete",
    "execute_operation_once",
    "receipt_reference",
    "receipt_object_id",
]
