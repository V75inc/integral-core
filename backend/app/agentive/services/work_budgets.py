"""Atomic root-shared reservations, not permission or dispatch authority.

Only trusted host price/usage resolvers may supply quotes or settlements.
No public API/tool or harness hook enables these services yet.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from jvspatial.core.context import graph_transaction
from pydantic import TypeAdapter, ValidationError

from app.agentive.services.work_mandates import load_approved_mandate_lineage
from app.agentive.services.work_outbox import (
    OBJECT_COLLECTION,
    _active_database,
    _hydrate_work_item,
    _is_postgres_txn_db,
)
from app.agentive.work_budget_models import WorkBudgetReservation
from app.agentive.work_models import WorkItem
from app.schemas.agentive.work import WorkError
from app.schemas.agentive.work_budget import (
    MandateReservationRequest,
    Money,
    money_units,
)
from app.schemas.agentive.work_mandate import WorkMandateRevision


async def _transaction_database():
    db = _active_database()
    if not _is_postgres_txn_db(db):
        raise WorkError("work.transaction_required")
    return db


def _ledger(plan: dict) -> dict:
    value = plan.get("mandate_budget")
    if value is None:
        return {
            "reserved_units": 0,
            "charged_units": 0,
            "model_requests": 0,
            "tool_calls": 0,
            "internal_writes": 0,
            "external_effects": 0,
            "capability_calls": {},
            "external_effects_by_capability": {},
        }
    if not isinstance(value, dict) or set(value) != {
        "reserved_units",
        "charged_units",
        "model_requests",
        "tool_calls",
        "internal_writes",
        "external_effects",
        "capability_calls",
        "external_effects_by_capability",
    }:
        raise WorkError("work.budget_invalid")
    result = dict(value)
    for key, amount in result.items():
        if key in {"capability_calls", "external_effects_by_capability"}:
            if not isinstance(amount, dict) or any(
                type(v) is not int or v < 0 for v in amount.values()
            ):
                raise WorkError("work.budget_invalid")
            result[key] = dict(amount)
        elif type(amount) is not int or amount < 0:
            raise WorkError("work.budget_invalid")
    return result


def _hydrate_reservation(document: dict) -> WorkBudgetReservation:
    """Reconcile stored identity, request digest and immutable cost hold."""
    try:
        record = WorkBudgetReservation(id=document["id"], **document["context"])
        request = MandateReservationRequest.model_validate(record.request)
        if (
            record.id != f"o.WorkBudgetReservation.{record.reservation_id}"
            or request.fingerprint() != record.request_fingerprint
            or request.quote.upper_cost is None
            or money_units(request.quote.upper_cost) != record.upper_units
            or (
                record.status == "reserved"
                and (record.charged_units or record.receipt_ref)
            )
            or (record.status != "reserved" and not record.receipt_ref.strip())
            or (
                record.status == "settled" and record.charged_units > record.upper_units
            )
            or (
                record.status == "overrun"
                and record.charged_units <= record.upper_units
            )
        ):
            raise WorkError("work.budget_invalid")
        expected_id = hashlib.sha256(
            f"{record.root_work_item_id}:{record.work_item_id}:{request.logical_effect_key}".encode()
        ).hexdigest()
        if expected_id != record.reservation_id:
            raise WorkError("work.budget_invalid")
        return record
    except (ValidationError, KeyError, TypeError, ValueError) as exc:
        raise WorkError("work.budget_invalid") from exc


def _assert_reviewed_request(
    request: MandateReservationRequest, revision: WorkMandateRevision
) -> None:
    """Replays retain the same reviewed route and effect scope as new holds."""
    if request.model_route:
        if request.model_route not in revision.model_routes or (
            request.quote.provider != request.model_route.provider
            or request.quote.model != request.model_route.model
            or request.quote.credential_ref != request.model_route.credential_ref
        ):
            raise WorkError("work.mandate_scope_denied")
        return
    grant = request.capability_grant
    if grant not in revision.grants:
        raise WorkError("work.mandate_scope_denied")
    if grant.operation == "external_effect":
        external = request.external_grant
        if external not in revision.external_grants or (
            external.capability_key != grant.capability_key
            or request.quote.provider != external.provider
            or request.quote.credential_ref != external.credential_ref
        ):
            raise WorkError("work.mandate_scope_denied")


async def _lock_root(
    db, root_id: str, principal_id: str, workspace_id: str, thread_id: str
):
    doc = await db.find_one_and_update(
        OBJECT_COLLECTION,
        {
            "id": f"o.WorkItem.{root_id}",
            "context.work_item_id": root_id,
            "context.principal_id": principal_id,
            "context.workspace_id": workspace_id,
            "context.thread_id": thread_id,
        },
        {"$set": {"context.updated_at": datetime.now(timezone.utc).isoformat()}},
    )
    if doc is None:
        raise WorkError("work.mandate_scope_denied")
    return _hydrate_work_item(doc)


async def _write_ledger(db: Any, root: WorkItem, fields: dict) -> None:
    """Persist under the shared root lock or roll back the reservation unit."""
    updated = await db.find_one_and_update(
        OBJECT_COLLECTION,
        {"id": root.id, "context.plan_revision": root.plan_revision},
        {"$set": fields},
    )
    if updated is None:
        raise WorkError("work.mandate_revision_conflict")


async def reserve_mandate_budget(
    *,
    work_item_id: str,
    principal_id: str,
    workspace_id: str,
    thread_id: str,
    request: MandateReservationRequest,
) -> WorkBudgetReservation:
    """Reserve global/per-capability counts and a known conservative USD hold.

    Quote identity/upper-bound validity must be supplied by a trusted resolver.
    Success here never authorizes dispatch; current permission, lease and root
    controls still need a fenced dispatch admission boundary.
    """
    try:
        request = MandateReservationRequest.model_validate(
            request.model_dump(mode="json")
        )
    except ValidationError as exc:
        raise WorkError("work.budget_invalid") from exc
    if request.quote.upper_cost is None:
        raise WorkError("work.cost_unavailable")
    db = await _transaction_database()
    async with graph_transaction(database=db) as graph:
        root, _ = await load_approved_mandate_lineage(
            work_item_id=work_item_id,
            principal_id=principal_id,
            workspace_id=workspace_id,
            thread_id=thread_id,
        )
        locked = await _lock_root(
            graph.database, root.work_item_id, principal_id, workspace_id, thread_id
        )
        # Re-read lineage/approval after locking root; concurrent root stop or
        # revision changes cannot bypass reservation accounting.
        root, revision = await load_approved_mandate_lineage(
            work_item_id=work_item_id,
            principal_id=principal_id,
            workspace_id=workspace_id,
            thread_id=thread_id,
        )
        if root.plan_revision != locked.plan_revision:
            raise WorkError("work.mandate_revision_conflict")
        _assert_reviewed_request(request, revision)
        ledger = _ledger(root.plan)
        reservation_id = hashlib.sha256(
            f"{root.work_item_id}:{work_item_id}:{request.logical_effect_key}".encode()
        ).hexdigest()
        object_id = f"o.WorkBudgetReservation.{reservation_id}"
        stored = await graph.database.get(OBJECT_COLLECTION, object_id)
        fingerprint = request.fingerprint()
        if stored is not None:
            record = _hydrate_reservation(stored)
            if (record.principal_id, record.workspace_id, record.thread_id) != (
                principal_id,
                workspace_id,
                thread_id,
            ):
                raise WorkError("work.mandate_scope_denied")
            if (
                record.request_fingerprint != fingerprint
                or record.review_digest != root.plan_revision
            ):
                raise WorkError("work.idempotency_conflict")
            if (
                record.status == "reserved"
                and ledger["reserved_units"] < record.upper_units
            ):
                raise WorkError("work.budget_invalid")
            return record
        if request.quote.valid_until <= datetime.now(timezone.utc):
            raise WorkError("work.cost_quote_expired")
        if request.model_route:
            ledger["model_requests"] += 1
        else:
            grant = request.capability_grant
            ledger["tool_calls"] += 1
            calls = ledger["capability_calls"].get(grant.capability_key, 0) + 1
            if calls > grant.max_calls:
                raise WorkError("work.budget_exhausted")
            ledger["capability_calls"][grant.capability_key] = calls
            if grant.operation == "internal_write":
                ledger["internal_writes"] += request.internal_write_units
            if grant.operation == "external_effect":
                external = request.external_grant
                effects = (
                    ledger["external_effects_by_capability"].get(
                        grant.capability_key, 0
                    )
                    + request.external_effect_units
                )
                if effects > external.max_effects:
                    raise WorkError("work.budget_exhausted")
                ledger["external_effects_by_capability"][grant.capability_key] = effects
                ledger["external_effects"] += request.external_effect_units
        for key in (
            "model_requests",
            "tool_calls",
            "internal_writes",
            "external_effects",
        ):
            if ledger[key] > getattr(revision.limits, f"max_{key}"):
                raise WorkError("work.budget_exhausted")
        upper = money_units(request.quote.upper_cost)
        ledger["reserved_units"] += upper
        if ledger["reserved_units"] + ledger["charged_units"] > money_units(
            revision.limits.max_spend
        ):
            raise WorkError("work.budget_exhausted")
        now = datetime.now(timezone.utc).isoformat()
        record = WorkBudgetReservation(
            id=object_id,
            reservation_id=reservation_id,
            root_work_item_id=root.work_item_id,
            work_item_id=work_item_id,
            principal_id=principal_id,
            workspace_id=workspace_id,
            thread_id=thread_id,
            review_digest=root.plan_revision,
            request_fingerprint=fingerprint,
            request=request.model_dump(mode="json"),
            upper_units=upper,
            created_at=now,
        )
        inserted = await graph.database.insert_if_absent(
            OBJECT_COLLECTION,
            {
                "id": object_id,
                "entity": "WorkBudgetReservation",
                "context": record.model_dump(exclude={"id"}),
            },
        )
        if not inserted.created:
            raise WorkError("work.cas_conflict")
        await _write_ledger(
            graph.database, root, {"context.plan.mandate_budget": ledger}
        )
        return record


async def settle_mandate_budget(
    *,
    reservation_id: str,
    principal_id: str,
    workspace_id: str,
    thread_id: str,
    actual_cost: Decimal | None,
    receipt_ref: str,
) -> WorkBudgetReservation:
    """Settle known terminal usage, including after cancellation.

    Unknown outcomes retain the hold. An overrun records the usage and requests
    a root stop; it never hides an incurred cost or fabricates a refund.
    """
    if actual_cost is None:
        raise WorkError("work.cost_unavailable")
    try:
        actual = money_units(TypeAdapter(Money).validate_python(actual_cost))
    except (ValidationError, ValueError) as exc:
        raise WorkError("work.budget_invalid") from exc
    if not receipt_ref or receipt_ref != receipt_ref.strip():
        raise WorkError("work.budget_receipt_required")
    db = await _transaction_database()
    async with graph_transaction(database=db) as graph:
        object_id = f"o.WorkBudgetReservation.{reservation_id}"
        stored = await graph.database.get(OBJECT_COLLECTION, object_id)
        if stored is None:
            raise WorkError("work.not_found")
        record = _hydrate_reservation(stored)
        if (record.principal_id, record.workspace_id, record.thread_id) != (
            principal_id,
            workspace_id,
            thread_id,
        ):
            raise WorkError("work.mandate_scope_denied")
        root = await _lock_root(
            graph.database,
            record.root_work_item_id,
            principal_id,
            workspace_id,
            thread_id,
        )
        # Reload after the shared lock, because another settlement may have won.
        stored = await graph.database.get(OBJECT_COLLECTION, object_id)
        record = _hydrate_reservation(stored)
        if record.status != "reserved":
            if record.charged_units == actual and record.receipt_ref == receipt_ref:
                return record
            raise WorkError("work.idempotency_conflict")
        if root.plan_revision != record.review_digest:
            raise WorkError("work.mandate_revision_conflict")
        ledger = _ledger(root.plan)
        if ledger["reserved_units"] < record.upper_units:
            raise WorkError("work.budget_invalid")
        ledger["reserved_units"] -= record.upper_units
        ledger["charged_units"] += actual
        now = datetime.now(timezone.utc).isoformat()
        record.status = "overrun" if actual > record.upper_units else "settled"
        record.charged_units = actual
        record.receipt_ref = receipt_ref
        record.settled_at = now
        fields = {"context.plan.mandate_budget": ledger}
        if record.status == "overrun":
            fields["context.cancel_requested_at"] = root.cancel_requested_at or now
        await _write_ledger(graph.database, root, fields)
        updated = await graph.database.find_one_and_update(
            OBJECT_COLLECTION,
            {"id": object_id, "context.status": "reserved"},
            {
                "$set": {
                    "context.status": record.status,
                    "context.charged_units": actual,
                    "context.receipt_ref": receipt_ref,
                    "context.settled_at": now,
                }
            },
        )
        if updated is None:
            raise WorkError("work.cas_conflict")
        return record
