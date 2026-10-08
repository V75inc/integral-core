"""Synthetic persisted receipts settle exact marked holds in real PostgreSQL.

No provider request, real price or public mandate approval is exercised here.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from jvspatial.core.context import (
    GraphContext,
    get_default_context,
    set_default_context,
)

from app.agentive.harness.contracts import (
    HarnessExecutionScope,
    ModelRouteIdentity,
    ModelUsageObservation,
    PhysicalModelRequest,
)
from app.agentive.harness.model_observations import persist_model_request_observation
from app.agentive.services import work_budgets, work_items
from app.agentive.services.work_model_receipts import settle_mandate_model_receipt
from app.schemas.agentive.work import WorkError
from tests.contract.test_work_budgets_postgres import (
    budget_root,
    leased_budget_child,
    ledger,
    reserve_leased,
)

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


def scope_for(context):
    return HarnessExecutionScope(
        tenant_id=context.workspace_id,
        principal_id=context.principal_id,
        workspace_id=context.workspace_id,
        thread_id=context.thread_id,
        session_id=f"synthetic-session-{uuid4().hex}",
        run_id=context.run_id,
        permission_revision="synthetic-permissions-v1",
        capability_version="synthetic-capabilities-v1",
    )


async def marked_case(case, *, bound=True):
    root, parent, leaf, context, req, db = case
    scope = scope_for(context)
    physical_id = uuid4().hex
    record = await reserve_leased(leaf, context, req)
    record = await work_budgets.mark_mandate_dispatch_intent(
        reservation_id=record.reservation_id,
        execution_context=context,
        request_fingerprint=req.fingerprint(),
        dispatch_ref=physical_id,
        model_scope=scope if bound else None,
    )
    now = datetime.now(timezone.utc)
    route = req.model_route
    intent = PhysicalModelRequest(
        request_id=physical_id,
        scope=scope,
        provider=route.provider,
        model=route.model,
        credential_source=route.credential_source,
        credential_ref=route.credential_ref,
        attempt=context.attempt,
        dispatched_at=now,
        observed_at=now,
        outcome="dispatch_intent",
    )
    terminal = intent.model_copy(
        update={
            "outcome": "responded",
            "observed_at": now + timedelta(milliseconds=1),
            "provider_request_id": f"synthetic-provider-request-{physical_id}",
            "usage": ModelUsageObservation(
                input_tokens=10,
                output_tokens=5,
                provider_cost_usd=Decimal("0.04"),
                cost_source="provider_response",
                complete=True,
            ),
        }
    )
    await persist_model_request_observation(intent)
    return record, intent, terminal


async def settle_receipt(record, **scope):
    return await settle_mandate_model_receipt(
        reservation_id=record.reservation_id,
        principal_id=scope.get("principal_id", "user-1"),
        workspace_id=scope.get("workspace_id", "workspace-1"),
        thread_id=scope.get("thread_id", "thread-1"),
    )


async def test_bound_receipt_settles_once_after_cancel_and_context_reload(
    leased_budget_child,
):
    root, parent, leaf, context, req, db = leased_budget_child
    record, intent, terminal = await marked_case(leased_budget_child)
    await persist_model_request_observation(terminal)
    await work_items.cancel_work_item(root.work_item_id)
    # Later mutable leaf state must not replace the dispatch's original run.
    await db.find_one_and_update(
        "object", {"id": leaf.id}, {"$set": {"context.attempt": 2}}
    )
    original = get_default_context()
    set_default_context(GraphContext(database=original.database))
    try:
        settled = await asyncio.gather(*(settle_receipt(record) for _ in range(3)))
    finally:
        set_default_context(original)
    assert all(item.status == "settled" for item in settled)
    assert all(item.charged_units == 4000000 for item in settled)
    assert all(
        item.model_dispatch_scope == intent.scope.model_dump() for item in settled
    )
    saved = await ledger(db, root)
    assert saved["reserved_units"] == 0
    assert saved["charged_units"] == 4000000
    assert saved["model_requests"] == 1


@pytest.mark.parametrize(
    "cost_source,cost,complete,provider_id",
    [
        ("unavailable", None, True, True),
        ("litellm_calculated", Decimal("0.04"), True, True),
        ("litellm_response", Decimal("0.04"), True, True),
        ("provider_response", Decimal("0.04"), False, True),
        ("provider_response", Decimal("0.04"), True, False),
    ],
)
async def test_estimated_or_incomplete_receipt_retains_hold(
    leased_budget_child, cost_source, cost, complete, provider_id
):
    root, parent, leaf, context, req, db = leased_budget_child
    record, intent, terminal = await marked_case(leased_budget_child)
    usage = terminal.usage.model_copy(
        update={
            "cost_source": cost_source,
            "provider_cost_usd": cost,
            "complete": complete,
        }
    )
    terminal = terminal.model_copy(
        update={
            "usage": usage,
            "provider_request_id": (
                terminal.provider_request_id if provider_id else None
            ),
        }
    )
    await persist_model_request_observation(terminal)
    with pytest.raises(WorkError) as exc:
        await settle_receipt(record)
    assert exc.value.code == "work.cost_unavailable"
    assert (await ledger(db, root))["reserved_units"] == 10000000
    assert (await ledger(db, root))["charged_units"] == 0


async def test_legacy_unbound_mark_cannot_borrow_a_model_receipt(leased_budget_child):
    root, parent, leaf, context, req, db = leased_budget_child
    record, intent, terminal = await marked_case(leased_budget_child, bound=False)
    await persist_model_request_observation(terminal)
    with pytest.raises(WorkError) as exc:
        await settle_receipt(record)
    assert exc.value.code == "work.model_receipt_required"
    assert (await ledger(db, root))["reserved_units"] == 10000000


@pytest.mark.parametrize(
    "field", ["provider", "model", "credential_ref", "credential_source"]
)
async def test_other_route_receipt_cannot_settle_hold(leased_budget_child, field):
    root, parent, leaf, context, req, db = leased_budget_child
    record, intent, terminal = await marked_case(leased_budget_child)
    value = "platform" if field == "credential_source" else "other-route"
    await persist_model_request_observation(terminal.model_copy(update={field: value}))
    with pytest.raises(WorkError) as exc:
        await settle_receipt(record)
    assert exc.value.code == "work.model_receipt_invalid"
    assert (await ledger(db, root))["reserved_units"] == 10000000


@pytest.mark.parametrize("field", ["principal_id", "workspace_id", "thread_id"])
async def test_other_owner_cannot_read_or_settle_receipt(leased_budget_child, field):
    root, parent, leaf, context, req, db = leased_budget_child
    record, intent, terminal = await marked_case(leased_budget_child)
    await persist_model_request_observation(terminal)
    with pytest.raises(WorkError) as exc:
        await settle_receipt(record, **{field: "other-owner"})
    assert exc.value.code == "work.mandate_scope_denied"
    assert (await ledger(db, root))["reserved_units"] == 10000000


@pytest.mark.parametrize("order", ["tie", "unknown_latest", "response_latest"])
async def test_unknown_outcome_order_preserves_reconciliation(
    leased_budget_child, order
):
    root, parent, leaf, context, req, db = leased_budget_child
    record, intent, terminal = await marked_case(leased_budget_child)
    offset = {"tie": 1, "unknown_latest": 2, "response_latest": 0}[order]
    unknown = intent.model_copy(
        update={
            "outcome": "outcome_unknown",
            "observed_at": intent.dispatched_at + timedelta(milliseconds=offset),
        }
    )
    await persist_model_request_observation(unknown)
    await persist_model_request_observation(terminal)
    if order == "response_latest":
        assert (await settle_receipt(record)).status == "settled"
    else:
        with pytest.raises(WorkError) as exc:
            await settle_receipt(record)
        assert exc.value.code == (
            "work.outcome_reconciliation_required"
            if order == "tie"
            else "work.cost_unavailable"
        )
        assert (await ledger(db, root))["reserved_units"] == 10000000


@pytest.mark.parametrize("cost", [Decimal("0"), Decimal("0.20")])
async def test_explicit_provider_zero_and_overrun_are_retained(
    leased_budget_child, cost
):
    root, parent, leaf, context, req, db = leased_budget_child
    record, intent, terminal = await marked_case(leased_budget_child)
    await persist_model_request_observation(
        terminal.model_copy(
            update={
                "usage": terminal.usage.model_copy(update={"provider_cost_usd": cost})
            }
        )
    )
    settled = await settle_receipt(record)
    assert settled.status == ("overrun" if cost > Decimal("0.10") else "settled")
    assert (await ledger(db, root))["charged_units"] == int(cost * 100000000)
    if cost:
        assert (await db.get("object", root.id))["context"]["cancel_requested_at"]


@pytest.mark.parametrize(
    "field", ["principal_id", "workspace_id", "thread_id", "run_id"]
)
async def test_foreign_model_scope_cannot_mark_dispatch(leased_budget_child, field):
    root, parent, leaf, context, req, db = leased_budget_child
    record = await reserve_leased(leaf, context, req)
    scope = scope_for(context)
    with pytest.raises(WorkError) as exc:
        await work_budgets.mark_mandate_dispatch_intent(
            reservation_id=record.reservation_id,
            execution_context=context,
            request_fingerprint=req.fingerprint(),
            dispatch_ref="synthetic-request",
            model_scope=scope.model_copy(update={field: "other-scope"}),
        )
    assert exc.value.code == "work.model_receipt_invalid"
    assert (await db.get("object", record.id))["context"][
        "dispatch_state"
    ] == "not_started"


async def test_route_identity_cannot_carry_credential_material():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ModelRouteIdentity(
            provider="provider-1",
            model="model-1",
            credential_source="platform",
            credential_ref="ref",
            api_key="synthetic-secret",
        )


@pytest.mark.parametrize("field", ["attempt", "dispatched_at", "scope"])
async def test_terminal_cannot_replace_original_dispatch_identity(
    leased_budget_child, field
):
    root, parent, leaf, context, req, db = leased_budget_child
    record, intent, terminal = await marked_case(leased_budget_child)
    replacements = {
        "attempt": intent.attempt + 1,
        "dispatched_at": intent.dispatched_at + timedelta(seconds=1),
        "scope": intent.scope.model_copy(update={"run_id": "other-run"}),
    }
    await persist_model_request_observation(
        terminal.model_copy(update={field: replacements[field]})
    )
    with pytest.raises(WorkError) as exc:
        await settle_receipt(record)
    assert exc.value.code == "work.model_receipt_invalid"
    assert (await ledger(db, root))["reserved_units"] == 10000000
    assert (await ledger(db, root))["charged_units"] == 0


async def test_missing_terminal_preserves_hold(leased_budget_child):
    root, parent, leaf, context, req, db = leased_budget_child
    record, intent, terminal = await marked_case(leased_budget_child)
    with pytest.raises(WorkError) as exc:
        await settle_receipt(record)
    assert exc.value.code == "work.model_receipt_required"
    assert (await ledger(db, root))["reserved_units"] == 10000000
    assert (await ledger(db, root))["charged_units"] == 0
