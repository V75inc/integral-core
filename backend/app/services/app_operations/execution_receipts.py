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
import re
from contextlib import nullcontext
from dataclasses import dataclass, replace
from itertools import islice
from typing import Any, AsyncContextManager, Awaitable, Callable, Dict, Iterable

from app.contracts.operations import OperationIdentity
from app.services.app_operations.transaction_scope import postgres_graph_transaction
from app.utils.time import utc_now_iso

_OBJECT_COLLECTION = "object"
_RECEIPT_ENTITY = "OperationExecutionReceipt"
_CONTRACT_FINGERPRINT = re.compile(r"[0-9a-f]{64}\Z")


class OperationReceiptConflict(ValueError):
    """An intent key was reused with different input or known effect contract."""


class OperationReceiptIncomplete(RuntimeError):
    """A legacy/incomplete receipt cannot safely be replayed."""


def _validate_contract_fingerprint(value: str | None) -> None:
    if value is not None and (
        type(value) is not str or _CONTRACT_FINGERPRINT.fullmatch(value) is None
    ):
        raise ValueError("An exact effect contract fingerprint is required")


def _record_contract_fingerprint(record: dict) -> str | None:
    body = record["context"]
    if "effect_contract_fingerprint" not in body:
        return None
    value = body["effect_contract_fingerprint"]
    try:
        if value is None:
            raise ValueError("Missing effect contract fingerprint")
        _validate_contract_fingerprint(value)
    except ValueError as exc:
        raise OperationReceiptIncomplete(
            "Operation effect contract is invalid"
        ) from exc
    return value


def _record_decision_fingerprint(record: dict) -> str | None:
    body = record["context"]
    if "decision_intent_fingerprint" not in body:
        return None
    value = body["decision_intent_fingerprint"]
    if value is None:
        raise OperationReceiptIncomplete("Original decision proof is invalid")
    try:
        _validate_contract_fingerprint(value)
    except ValueError as exc:
        raise OperationReceiptIncomplete("Original decision proof is invalid") from exc
    return value


@dataclass(frozen=True)
class OperationExecutionResult:
    """A committed command result or an exact replay of one."""

    result: Dict[str, Any]
    replayed: bool
    identity: OperationIdentity | None = None


@dataclass(frozen=True, repr=False)
class OperationOutcomeObservation:
    """Private journal proof, with no saved output or current-state assertion.

    Export only ``reference()`` after current own-history admission. The identity
    carries private idempotency material and is not a public response model.
    """

    identity: OperationIdentity

    def reference(self) -> Dict[str, Any]:
        return receipt_reference(self.identity, replayed=True)


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
    effect_contract_fingerprint: str | None = None,
    decision_intent_fingerprint: str | None = None,
) -> Dict[str, Any]:
    _validate_contract_fingerprint(effect_contract_fingerprint)
    _validate_contract_fingerprint(decision_intent_fingerprint)
    record: Dict[str, Any] = {
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
    if effect_contract_fingerprint is not None:
        record["context"]["effect_contract_fingerprint"] = effect_contract_fingerprint
    if decision_intent_fingerprint is not None:
        record["context"]["decision_intent_fingerprint"] = decision_intent_fingerprint
    return record


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


async def lookup_operation_outcome(
    *,
    identity: OperationIdentity,
    request_hash: str,
    principal_aliases: Iterable[str] = (),
    database: Any | None = None,
    expected_effect_contract_fingerprint: str | None = None,
    expected_decision_intent_fingerprint: str | None = None,
) -> OperationOutcomeObservation | None:
    """Observe one original success without claiming, repairing or replaying it.

    This is a storage primitive, not authorization. Its caller must supply a
    currently admitted identity/workspace and at most one verified human alias;
    re-admit own-history access before releasing even the opaque reference.
    Never pass client-selected aliases or serialize this private return object.

    One native transaction batch read observes both deterministic IDs together,
    bypassing identity caches. Missing history returns None, never permission to
    execute. Incomplete/conflicting/corrupt rows and outages remain explicit.
    Saved output is deliberately not decoded, including legacy malformed output.
    Requiring an expected effect contract also requires original independent
    metadata: legacy absence is incomplete proof, never inferred from output.
    """
    _validate_contract_fingerprint(expected_effect_contract_fingerprint)
    _validate_contract_fingerprint(expected_decision_intent_fingerprint)
    if not isinstance(identity, OperationIdentity):
        raise TypeError("an OperationIdentity is required")
    if any(
        type(value) is not str or not value or value != value.strip()
        for value in identity.cache_key()
    ):
        raise ValueError("operation identity requires exact nonempty values")
    if type(request_hash) is not str or not request_hash:
        raise ValueError("request_hash is required")
    if isinstance(principal_aliases, (str, bytes)):
        raise ValueError("operation aliases require at most one verified principal")
    aliases = tuple(islice(principal_aliases, 2))
    if len(aliases) > 1 or any(
        type(alias) is not str or not alias or alias != alias.strip()
        for alias in aliases
    ):
        raise ValueError("operation aliases require at most one verified principal")
    identities = {receipt_object_id(identity): identity}
    for alias in aliases:
        candidate = replace(identity, principal_id=alias)
        identities[receipt_object_id(candidate)] = candidate

    async with postgres_graph_transaction(database) as transaction:
        records = await transaction.find_many(_OBJECT_COLLECTION, sorted(identities))
        if not isinstance(records, dict) or not set(records) <= identities.keys():
            raise OperationReceiptIncomplete("Operation receipt lookup is invalid")
        for receipt_id, record in records.items():
            if not isinstance(record, dict):
                raise OperationReceiptIncomplete(
                    "Operation receipt identity is invalid"
                )
            _validate_receipt_identity(record, identities[receipt_id])
            _record_contract_fingerprint(record)
            _record_decision_fingerprint(record)
            if record["context"].get("request_hash") != request_hash:
                raise OperationReceiptConflict(
                    "Idempotency key reused with different payload"
                )
        if not records:
            return None
        completed = [
            key
            for key, row in records.items()
            if row["context"].get("status") == "succeeded"
        ]
        if len(completed) != 1:
            raise OperationReceiptIncomplete(
                "Operation outcome requires reconciliation"
            )
        original_id = completed[0]
        original_contract = _record_contract_fingerprint(records[original_id])
        if expected_effect_contract_fingerprint is not None:
            if original_contract is None:
                raise OperationReceiptIncomplete(
                    "Original effect contract requires reconciliation"
                )
            if original_contract != expected_effect_contract_fingerprint:
                raise OperationReceiptConflict(
                    "Idempotency key reused with a different effect contract"
                )
        original_decision = _record_decision_fingerprint(records[original_id])
        if expected_decision_intent_fingerprint is not None:
            if original_decision is None:
                raise OperationReceiptIncomplete(
                    "Original decision proof requires reconciliation"
                )
            if original_decision != expected_decision_intent_fingerprint:
                raise OperationReceiptConflict(
                    "Operation belongs to a different reviewed proposal"
                )
        for receipt_id, row in records.items():
            if receipt_id == original_id:
                continue
            context = row["context"]
            if (
                context.get("status") != "alias"
                or context.get("canonical_receipt_id") != original_id
                or context.get("result_json") != ""
                or (
                    _record_contract_fingerprint(row) is not None
                    and _record_contract_fingerprint(row) != original_contract
                )
                or (
                    _record_decision_fingerprint(row) is not None
                    and _record_decision_fingerprint(row) != original_decision
                )
            ):
                raise OperationReceiptIncomplete(
                    "Operation alias receipt requires reconciliation"
                )
        return OperationOutcomeObservation(identities[original_id])


async def execute_operation_once(
    *,
    identity: OperationIdentity,
    request_hash: str,
    execute: Callable[[], Awaitable[Dict[str, Any]]],
    event_outbox: Iterable[Dict[str, Any]] | None = None,
    database: Any | None = None,
    principal_aliases: Iterable[str] = (),
    admit_identity: Callable[[], AsyncContextManager[None]] | None = None,
    effect_contract_fingerprint: str | None = None,
    decision_intent_fingerprint: str | None = None,
) -> OperationExecutionResult:
    """Execute a local command once and commit its receipt with graph effects.

    The optional server-supplied effect contract is persisted on new receipts;
    known mismatches conflict. Legacy replay never guesses or backfills it. Use
    lookup with an expected contract when strict original-declaration proof is
    required; replay compatibility alone does not establish such evidence.

    ``execute`` runs only after this invocation wins the deterministic receipt
    claim.  It inherits the transaction-bound default graph context, so normal
    jvspatial node and edge writes participate in the same commit.
    """
    _validate_contract_fingerprint(effect_contract_fingerprint)
    _validate_contract_fingerprint(decision_intent_fingerprint)
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
                identity=candidate,
                request_hash=request_hash,
                status="claimed",
                effect_contract_fingerprint=effect_contract_fingerprint,
                decision_intent_fingerprint=decision_intent_fingerprint,
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
                _record_contract_fingerprint(record)
                _record_decision_fingerprint(record)
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
            source_contract = (
                _record_contract_fingerprint(existing[source_id])
                if completed
                else effect_contract_fingerprint
            )
            if (
                source_contract is not None
                and effect_contract_fingerprint is not None
                and source_contract != effect_contract_fingerprint
            ):
                raise OperationReceiptConflict(
                    "Idempotency key reused with a different effect contract"
                )
            source_decision = (
                _record_decision_fingerprint(existing[source_id])
                if completed
                else decision_intent_fingerprint
            )
            if decision_intent_fingerprint is not None and completed:
                if source_decision is None:
                    raise OperationReceiptIncomplete(
                        "Original decision proof requires reconciliation"
                    )
                if source_decision != decision_intent_fingerprint:
                    raise OperationReceiptConflict(
                        "Operation belongs to a different reviewed proposal"
                    )
            for receipt_id, row in existing.items():
                if receipt_id == source_id:
                    continue
                context = row["context"]
                if (
                    context.get("status") != "alias"
                    or context.get("canonical_receipt_id") != source_id
                    or context.get("result_json") != ""
                    or (
                        _record_contract_fingerprint(row) is not None
                        and _record_contract_fingerprint(row) != source_contract
                    )
                    or (
                        _record_decision_fingerprint(row) is not None
                        and _record_decision_fingerprint(row) != source_decision
                    )
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
                        effect_contract_fingerprint=source_contract,
                        decision_intent_fingerprint=source_decision,
                    ),
                )
            for receipt_id in sorted(created - {source_id}):
                marker = _receipt_document(
                    identity=identities[receipt_id],
                    request_hash=request_hash,
                    status="alias",
                    effect_contract_fingerprint=source_contract,
                    decision_intent_fingerprint=source_decision,
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
    "OperationOutcomeObservation",
    "OperationReceiptConflict",
    "OperationReceiptIncomplete",
    "execute_operation_once",
    "lookup_operation_outcome",
    "receipt_reference",
    "receipt_object_id",
]
