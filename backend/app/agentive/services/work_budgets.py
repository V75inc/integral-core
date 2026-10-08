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

from app.agentive.harness.contracts import HarnessExecutionScope
from app.agentive.services.work_execution import deterministic_run_id, effect_key
from app.agentive.services.work_mandates import load_approved_mandate_path
from app.agentive.services.work_outbox import (
    OBJECT_COLLECTION,
    _active_database,
    _hydrate_work_item,
    _is_postgres_txn_db,
)
from app.agentive.work_budget_models import WorkBudgetReservation
from app.agentive.work_models import WorkItem
from app.schemas.agentive.work import WorkError, WorkExecutionContext
from app.schemas.agentive.work_budget import (
    MandateReservationRequest,
    Money,
    money_units,
)
from app.schemas.agentive.work_mandate import (
    MandateExternalGrant,
    WorkMandateRevision,
)


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
            "external_effects_by_grant": {},
        }
    # Legacy ledgers without external effects can upgrade without attribution
    # guesses. Existing external counts need explicit reconciliation first.
    if isinstance(value, dict) and "external_effects_by_grant" not in value:
        if value.get("external_effects") != 0:
            raise WorkError("work.budget_attribution_required")
        value = {**value, "external_effects_by_grant": {}}
    if not isinstance(value, dict) or set(value) != {
        "reserved_units",
        "charged_units",
        "model_requests",
        "tool_calls",
        "internal_writes",
        "external_effects",
        "capability_calls",
        "external_effects_by_capability",
        "external_effects_by_grant",
    }:
        raise WorkError("work.budget_invalid")
    result = dict(value)
    for key, amount in result.items():
        if key in {
            "capability_calls",
            "external_effects_by_capability",
            "external_effects_by_grant",
        }:
            if not isinstance(amount, dict) or any(
                type(v) is not int or v < 0 for v in amount.values()
            ):
                raise WorkError("work.budget_invalid")
            result[key] = dict(amount)
        elif type(amount) is not int or amount < 0:
            raise WorkError("work.budget_invalid")
    if any(
        sum(result[key].values()) != result["external_effects"]
        for key in ("external_effects_by_capability", "external_effects_by_grant")
    ):
        raise WorkError("work.budget_invalid")
    return result


def _external_grant_key(grant: MandateExternalGrant) -> str:
    """Bind a destination count to its complete reviewed provider/grant tuple."""
    return hashlib.sha256(grant.model_dump_json().encode()).hexdigest()


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
        marked = record.dispatch_state in {"intent", "unknown", "completed"}
        if (
            marked != bool(record.dispatch_ref and record.dispatched_at)
            or (not marked and (record.dispatch_ref or record.dispatched_at))
            or (record.dispatch_state == "unknown" and not record.outcome_ref)
            or (
                record.dispatch_state == "completed"
                and record.status not in {"settled", "overrun"}
            )
            or (
                record.dispatch_state == "not_dispatched"
                and record.status != "released"
            )
            or (
                record.status == "released"
                and (record.dispatch_state != "not_dispatched" or record.charged_units)
            )
            or (
                record.dispatch_state in {"intent", "unknown"}
                and record.status != "reserved"
            )
        ):
            raise WorkError("work.budget_invalid")
        if (
            (
                record.dispatch_state in {"legacy_untracked", "not_started", "intent"}
                and record.outcome_ref
            )
            or (
                record.dispatch_state in {"completed", "not_dispatched"}
                and record.outcome_ref != record.receipt_ref
            )
            or any(
                v != v.strip() or len(v) > 255
                for v in (record.dispatch_ref, record.outcome_ref)
            )
        ):
            raise WorkError("work.budget_invalid")
        if marked:
            when = datetime.fromisoformat(record.dispatched_at.replace("Z", "+00:00"))
            if when.utcoffset() is None:
                raise WorkError("work.budget_invalid")
        if record.model_dispatch_scope:
            scope = HarnessExecutionScope.model_validate(record.model_dispatch_scope)
            if (
                not marked
                or request.model_route is None
                or record.model_dispatch_attempt < 1
                or scope.principal_id != record.principal_id
                or scope.workspace_id != record.workspace_id
                or scope.thread_id != record.thread_id
                or scope.run_id
                != deterministic_run_id(
                    work_item_id=record.work_item_id,
                    attempt=record.model_dispatch_attempt,
                )
            ):
                raise WorkError("work.budget_invalid")
        elif record.model_dispatch_attempt:
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


async def _lock_work_scope(
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


def _assert_leased_reservation(
    context: WorkExecutionContext,
    item: WorkItem,
    request: MandateReservationRequest,
) -> None:
    """Check fresh, row-locked authority, not a cached worker snapshot."""
    if context.cancellation_signal:
        raise WorkError("work.cancelled")
    if (
        item.status != "running"
        or item.attempt != context.attempt
        or item.principal_id != context.principal_id
        or item.workspace_id != context.workspace_id
        or (item.thread_id or "") != context.thread_id
        or item.lease_token != context.lease_token
        or item.lease_fence != context.lease_fence
        or (item.deadline_at or None) != context.deadline_at
        or context.run_id
        != deterministic_run_id(work_item_id=item.work_item_id, attempt=item.attempt)
    ):
        raise WorkError("work.lease_lost")
    try:
        expiry = datetime.fromisoformat(item.lease_expires_at.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError) as exc:
        raise WorkError("work.lease_lost") from exc
    if expiry.utcoffset() is None or expiry <= datetime.now(timezone.utc):
        raise WorkError("work.lease_lost")
    if (
        context.effect_key
        != effect_key(
            work_item_id=item.work_item_id, logical_step_key=context.logical_step_key
        )
        or request.logical_effect_key != context.effect_key
    ):
        raise WorkError("work.logical_step_conflict")


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
    execution_context: WorkExecutionContext | None = None,
) -> WorkBudgetReservation:
    """Reserve global/per-capability counts and a known conservative USD hold.

    Quote identity/upper-bound validity must be supplied by a trusted resolver.
    Optional server-derived execution context fences the root-to-leaf rows and
    checks current leaf lease/effect identity in the accounting transaction.
    Success still never authorizes dispatch: current permissions, price
    provenance, dispatch marking and physical-boundary hooks remain required.
    """
    try:
        request = MandateReservationRequest.model_validate(
            request.model_dump(mode="json")
        )
    except ValidationError as exc:
        raise WorkError("work.budget_invalid") from exc
    if execution_context is not None:
        try:
            execution_context = WorkExecutionContext.model_validate(
                execution_context.model_dump()
            )
        except (ValidationError, AttributeError) as exc:
            raise WorkError("work.lease_lost") from exc
        if (
            execution_context.work_item_id != work_item_id
            or execution_context.principal_id != principal_id
            or execution_context.workspace_id != workspace_id
            or execution_context.thread_id != thread_id
        ):
            raise WorkError("work.mandate_scope_denied")
    if request.quote.upper_cost is None:
        raise WorkError("work.cost_unavailable")
    db = await _transaction_database()
    async with graph_transaction(database=db) as graph:
        root, _, path = await load_approved_mandate_path(
            work_item_id=work_item_id,
            principal_id=principal_id,
            workspace_id=workspace_id,
            thread_id=thread_id,
        )
        # Root-to-leaf order is shared by all siblings. Locks live until the
        # reservation transaction commits; no provider call holds these locks.
        locked_path = []
        for item in path if execution_context is not None else (root,):
            locked_path.append(
                await _lock_work_scope(
                    graph.database,
                    item.work_item_id,
                    principal_id,
                    workspace_id,
                    thread_id,
                )
            )
        locked = locked_path[0]
        # Re-read lineage/approval after locking root; concurrent root stop or
        # revision changes cannot bypass reservation accounting.
        root, revision, current_path = await load_approved_mandate_path(
            work_item_id=work_item_id,
            principal_id=principal_id,
            workspace_id=workspace_id,
            thread_id=thread_id,
        )
        if root.plan_revision != locked.plan_revision:
            raise WorkError("work.mandate_revision_conflict")
        if execution_context is not None:
            if tuple(item.work_item_id for item in current_path) != tuple(
                item.work_item_id for item in locked_path
            ):
                raise WorkError("work.mandate_lineage_invalid")
            _assert_leased_reservation(execution_context, current_path[-1], request)
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
            if record.status == "released":
                raise WorkError("work.reservation_released")
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
                destination_key = _external_grant_key(external)
                effects = (
                    ledger["external_effects_by_grant"].get(destination_key, 0)
                    + request.external_effect_units
                )
                if effects > external.max_effects:
                    raise WorkError("work.budget_exhausted")
                ledger["external_effects_by_grant"][destination_key] = effects
                ledger["external_effects_by_capability"][grant.capability_key] = (
                    ledger["external_effects_by_capability"].get(
                        grant.capability_key, 0
                    )
                    + request.external_effect_units
                )
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
            dispatch_state=(
                "not_started" if execution_context is not None else "legacy_untracked"
            ),
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
        root = await _lock_work_scope(
            graph.database,
            record.root_work_item_id,
            principal_id,
            workspace_id,
            thread_id,
        )
        # Reload after the shared lock, because another settlement may have won.
        stored = await graph.database.get(OBJECT_COLLECTION, object_id)
        record = _hydrate_reservation(stored)
        if record.status == "released":
            raise WorkError("work.reservation_released")
        if record.dispatch_state == "not_started":
            raise WorkError("work.dispatch_intent_required")
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
        if record.dispatch_state in {"intent", "unknown"}:
            record.dispatch_state = "completed"
            record.outcome_ref = receipt_ref
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
                    "context.dispatch_state": record.dispatch_state,
                    "context.outcome_ref": record.outcome_ref,
                }
            },
        )
        if updated is None:
            raise WorkError("work.cas_conflict")
        return record


async def _read_scoped_reservation(
    db, reservation_id, principal_id, workspace_id, thread_id
):
    stored = await db.get(
        OBJECT_COLLECTION, f"o.WorkBudgetReservation.{reservation_id}"
    )
    if stored is None:
        raise WorkError("work.not_found")
    record = _hydrate_reservation(stored)
    if (record.principal_id, record.workspace_id, record.thread_id) != (
        principal_id,
        workspace_id,
        thread_id,
    ):
        raise WorkError("work.mandate_scope_denied")
    return record


def _evidence_ref(value: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 255
    ):
        raise WorkError("work.budget_receipt_required")


async def mark_mandate_dispatch_intent(
    *,
    reservation_id: str,
    execution_context: WorkExecutionContext,
    request_fingerprint: str,
    dispatch_ref: str,
    model_scope: HarnessExecutionScope | None = None,
) -> WorkBudgetReservation:
    """Persist one fenced dispatch intent; never repeat an uncertain operation.

    This is an internal coordination primitive, not a permission grant. The
    physical adapter must first resolve current authority and trusted pricing.
    After commit it may attempt the one marked operation; crash/timeout leaves
    the hold and requires outcome reconciliation, not another intent.
    """
    _evidence_ref(dispatch_ref)
    try:
        ctx = WorkExecutionContext.model_validate(execution_context.model_dump())
    except (ValidationError, AttributeError) as exc:
        raise WorkError("work.lease_lost") from exc
    db = await _transaction_database()
    async with graph_transaction(database=db) as graph:
        record = await _read_scoped_reservation(
            graph.database,
            reservation_id,
            ctx.principal_id,
            ctx.workspace_id,
            ctx.thread_id,
        )
        if record.work_item_id != ctx.work_item_id:
            raise WorkError("work.mandate_scope_denied")
        root, _, path = await load_approved_mandate_path(
            work_item_id=ctx.work_item_id,
            principal_id=ctx.principal_id,
            workspace_id=ctx.workspace_id,
            thread_id=ctx.thread_id,
        )
        locked_ids = []
        for item in path:
            await _lock_work_scope(
                graph.database,
                item.work_item_id,
                ctx.principal_id,
                ctx.workspace_id,
                ctx.thread_id,
            )
            locked_ids.append(item.work_item_id)
        root, revision, path = await load_approved_mandate_path(
            work_item_id=ctx.work_item_id,
            principal_id=ctx.principal_id,
            workspace_id=ctx.workspace_id,
            thread_id=ctx.thread_id,
        )
        if locked_ids != [item.work_item_id for item in path]:
            raise WorkError("work.mandate_lineage_invalid")
        record = await _read_scoped_reservation(
            graph.database,
            reservation_id,
            ctx.principal_id,
            ctx.workspace_id,
            ctx.thread_id,
        )
        if (
            record.root_work_item_id != root.work_item_id
            or record.review_digest != root.plan_revision
        ):
            raise WorkError("work.mandate_revision_conflict")
        if record.request_fingerprint != request_fingerprint:
            raise WorkError("work.idempotency_conflict")
        req = MandateReservationRequest.model_validate(record.request)
        _assert_leased_reservation(ctx, path[-1], req)
        _assert_reviewed_request(req, revision)
        scope_payload = {}
        if model_scope is not None:
            try:
                scope = HarnessExecutionScope.model_validate(model_scope.model_dump())
            except (ValidationError, AttributeError) as exc:
                raise WorkError("work.model_receipt_invalid") from exc
            if (
                req.model_route is None
                or scope.principal_id != ctx.principal_id
                or scope.workspace_id != ctx.workspace_id
                or scope.thread_id != ctx.thread_id
                or scope.run_id != ctx.run_id
            ):
                raise WorkError("work.model_receipt_invalid")
            scope_payload = scope.model_dump(mode="json")
        if record.status == "released":
            raise WorkError("work.reservation_released")
        if record.status != "reserved" or record.dispatch_state != "not_started":
            raise WorkError("work.outcome_reconciliation_required")
        if req.quote.valid_until <= datetime.now(timezone.utc):
            raise WorkError("work.cost_quote_expired")
        if _ledger(root.plan)["reserved_units"] < record.upper_units:
            raise WorkError("work.budget_invalid")
        now = datetime.now(timezone.utc).isoformat()
        saved = await graph.database.find_one_and_update(
            OBJECT_COLLECTION,
            {
                "id": record.id,
                "context.status": "reserved",
                "context.dispatch_state": "not_started",
            },
            {
                "$set": {
                    "context.dispatch_state": "intent",
                    "context.dispatch_ref": dispatch_ref,
                    "context.dispatched_at": now,
                    "context.model_dispatch_scope": scope_payload,
                    "context.model_dispatch_attempt": (
                        ctx.attempt if scope_payload else 0
                    ),
                }
            },
        )
        if saved is None:
            raise WorkError("work.cas_conflict")
        return _hydrate_reservation(saved)


async def reconcile_mandate_dispatch(
    *,
    reservation_id: str,
    principal_id: str,
    workspace_id: str,
    thread_id: str,
    outcome: str,
    evidence_ref: str,
    dispatch_ref: str = "",
) -> WorkBudgetReservation:
    """Record uncertainty or release only a never-marked reservation.

    Cancellation does not prevent outcome accounting. References must come
    from a trusted host coordinator; no public/model-issued receipt is accepted
    by an API. Intent/unknown/legacy history cannot prove no dispatch and cannot
    be refunded here, even if a caller says that the operation did not run.
    Admission volume counters remain consumed when a monetary hold is released.
    """
    _evidence_ref(evidence_ref)
    if outcome not in {"unknown", "not_dispatched"}:
        raise WorkError("work.budget_invalid")
    db = await _transaction_database()
    async with graph_transaction(database=db) as graph:
        record = await _read_scoped_reservation(
            graph.database, reservation_id, principal_id, workspace_id, thread_id
        )
        root = await _lock_work_scope(
            graph.database,
            record.root_work_item_id,
            principal_id,
            workspace_id,
            thread_id,
        )
        record = await _read_scoped_reservation(
            graph.database, reservation_id, principal_id, workspace_id, thread_id
        )
        if root.plan_revision != record.review_digest:
            raise WorkError("work.mandate_revision_conflict")
        if outcome == "unknown":
            if record.status != "reserved" or record.dispatch_state not in {
                "intent",
                "unknown",
            }:
                raise WorkError("work.outcome_reconciliation_required")
            if not dispatch_ref or dispatch_ref != record.dispatch_ref:
                raise WorkError("work.idempotency_conflict")
            if record.dispatch_state == "unknown":
                if record.outcome_ref == evidence_ref:
                    return record
                raise WorkError("work.idempotency_conflict")
            fields = {
                "context.dispatch_state": "unknown",
                "context.outcome_ref": evidence_ref,
            }
        else:
            if dispatch_ref:
                raise WorkError("work.outcome_reconciliation_required")
            if record.status == "released":
                if record.receipt_ref == evidence_ref:
                    return record
                raise WorkError("work.idempotency_conflict")
            if record.status != "reserved" or record.dispatch_state != "not_started":
                raise WorkError("work.outcome_reconciliation_required")
            account = _ledger(root.plan)
            if account["reserved_units"] < record.upper_units:
                raise WorkError("work.budget_invalid")
            account["reserved_units"] -= record.upper_units
            await _write_ledger(
                graph.database, root, {"context.plan.mandate_budget": account}
            )
            fields = {
                "context.status": "released",
                "context.dispatch_state": "not_dispatched",
                "context.receipt_ref": evidence_ref,
                "context.outcome_ref": evidence_ref,
                "context.settled_at": datetime.now(timezone.utc).isoformat(),
            }
        saved = await graph.database.find_one_and_update(
            OBJECT_COLLECTION,
            {"id": record.id, "context.status": "reserved"},
            {"$set": fields},
        )
        if saved is None:
            raise WorkError("work.cas_conflict")
        return _hydrate_reservation(saved)
