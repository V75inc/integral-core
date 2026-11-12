"""Internal settlement from persisted model evidence, never caller-issued money.

This does not confer dispatch authority, resolve prices, or expose an API/tool.
Unknown or calculated costs retain the reserved hold for reconciliation.
"""

from __future__ import annotations

import logging
from datetime import datetime

from pydantic import ValidationError

from app.agentive.harness.contracts import HarnessExecutionScope, ModelRouteIdentity
from app.agentive.harness.jvspatial_store import HarnessPersistenceError
from app.agentive.harness.model_observations import (
    _record_id,
    _scope_key,
    load_bound_model_request_observation,
)
from app.agentive.services.work_budgets import (
    _read_scoped_reservation,
    _transaction_database,
    reconcile_mandate_dispatch,
    settle_mandate_budget,
)
from app.agentive.work_budget_models import WorkBudgetReservation
from app.models.harness_records import HarnessModelRequestRecord
from app.schemas.agentive.work import TERMINAL_WORK_STATUSES, WorkError
from app.schemas.agentive.work_budget import MandateReservationRequest

logger = logging.getLogger(__name__)


async def reconcile_retained_model_receipts(
    cursor: dict[str, str], *, limit: int = 20
) -> int:
    """Settle receipts or release terminal, undispatched holds in bounded pages."""
    from app.services.app_operations.transaction_scope import (
        graph_transaction_available,
    )

    if not graph_transaction_available():
        return 0
    limit = max(1, min(limit, 100))
    db = await _transaction_database()
    query = {
        "entity": "WorkBudgetReservation",
        "context.status": "reserved",
        "$or": [
            {
                "context.dispatch_state": {"$in": ["intent", "unknown"]},
                "context.model_dispatch_scope.run_id": {"$exists": True},
            },
            {
                "context.dispatch_state": "not_started",
                "context.request.model_route.model": {"$exists": True},
            },
        ],
    }
    after = cursor.get("after")
    if after:
        query["id"] = {"$gt": after}
    rows = await db.find("object", query, sort=[("id", 1)], limit=limit)
    settled = 0
    for row in rows:
        data = row["context"]
        try:
            scope = {
                "reservation_id": data["reservation_id"],
                "principal_id": data["principal_id"],
                "workspace_id": data["workspace_id"],
                "thread_id": data["thread_id"],
            }
            if data["dispatch_state"] == "not_started":
                # Skip active work without taking a write lock. The release
                # path rechecks terminal status and dispatch state under locks.
                row = await db.get("object", f"o.WorkItem.{data['work_item_id']}")
                if (row or {}).get("context", {}).get(
                    "status"
                ) not in TERMINAL_WORK_STATUSES:
                    continue
                await reconcile_mandate_dispatch(
                    **scope,
                    outcome="not_dispatched",
                    evidence_ref=f"terminal-before-dispatch:{data['work_item_id']}",
                    require_terminal_work=True,
                )
            else:
                await settle_mandate_model_receipt(**scope)
        except (KeyError, ValidationError):
            logger.warning("Retained model reservation has invalid accounting fields")
        except WorkError as exc:
            expected = {"work.model_receipt_required", "work.cost_unavailable"}
            if data.get("dispatch_state") == "not_started":
                expected.update(
                    {
                        "work.reservation_owner_active",
                        "work.outcome_reconciliation_required",
                        "work.idempotency_conflict",
                    }
                )
            if exc.code not in expected:
                logger.warning(
                    "Retained model reservation could not reconcile: %s", exc.code
                )
        else:
            settled += 1
    if len(rows) == limit:
        cursor["after"] = rows[-1]["id"]
    else:
        cursor.pop("after", None)
    return settled


async def settle_mandate_model_receipt(
    *,
    reservation_id: str,
    principal_id: str,
    workspace_id: str,
    thread_id: str,
) -> WorkBudgetReservation:
    """Settle a bound marked request from its latest definitive provider cost.

    The authenticated host supplies ownership only. Scope, route, physical
    request identity, cost and receipt reference come from durable facts.
    Legacy unbound holds, contradictory timestamps and incomplete/estimated
    costs cannot establish settlement. Accounting can complete after Stop
    without authorizing another request or changing its saved run scope.
    """
    db = await _transaction_database()
    record = await _read_scoped_reservation(
        db, reservation_id, principal_id, workspace_id, thread_id
    )
    request = MandateReservationRequest.model_validate(record.request)
    if (
        not record.model_dispatch_scope
        or request.model_route is None
        or record.dispatch_state not in {"intent", "unknown", "completed"}
    ):
        raise WorkError("work.model_receipt_required")
    scope = HarnessExecutionScope.model_validate(record.model_dispatch_scope)
    route = ModelRouteIdentity.model_validate(request.model_route.model_dump())
    observations = []
    admission_time = datetime.fromisoformat(record.dispatched_at)
    try:
        for outcome in ("responded", "failed", "cancelled", "outcome_unknown"):
            receipt_id = _record_id(_scope_key(scope), record.dispatch_ref, outcome)
            if await HarnessModelRequestRecord.get(receipt_id) is None:
                continue
            observation = await load_bound_model_request_observation(
                scope=scope,
                request_id=record.dispatch_ref,
                outcome=outcome,
                expected_route=route,
            )
            if (
                observation.observed_at is None
                or observation.observed_at < admission_time
            ):
                raise WorkError("work.model_receipt_invalid")
            observations.append((observation, receipt_id))
    except HarnessPersistenceError as exc:
        raise WorkError("work.model_receipt_invalid") from exc
    if not observations:
        raise WorkError("work.model_receipt_required")
    observations.sort(key=lambda item: item[0].observed_at)
    terminal, receipt_id = observations[-1]
    if len(observations) > 1 and (
        observations[-2][0].observed_at == terminal.observed_at
    ):
        raise WorkError("work.outcome_reconciliation_required")
    outcomes = {observation.outcome for observation, _ in observations}
    if {"failed", "responded"} <= outcomes:
        raise WorkError("work.outcome_reconciliation_required")
    usage = terminal.usage
    if (
        terminal.outcome != "responded"
        or not terminal.provider_request_id
        or usage is None
        or not usage.complete
        or usage.cost_source != "provider_response"
        or usage.provider_cost_usd is None
    ):
        raise WorkError("work.cost_unavailable")
    return await settle_mandate_budget(
        reservation_id=reservation_id,
        principal_id=principal_id,
        workspace_id=workspace_id,
        thread_id=thread_id,
        actual_cost=usage.provider_cost_usd,
        receipt_ref=receipt_id,
    )
