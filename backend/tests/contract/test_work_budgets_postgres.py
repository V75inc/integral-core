"""Store-level shared budget proof; no harness dispatch or real provider calls."""

from __future__ import annotations

import asyncio
import hashlib
from decimal import Decimal
from uuid import uuid4

import pytest

from app.agentive.services import work_budgets, work_items, work_outbox
from app.schemas.agentive.work import WorkError
from app.schemas.agentive.work_budget import MandateReservationRequest
from tests.test_work_mandate_contract import _propose_review, mandate_payload

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


async def approved_root(payload):
    payload["mandate_id"] = f"budget-{uuid4().hex}"
    root, approval = await _propose_review(payload)
    db = work_outbox._txn_database(work_outbox._active_database())
    # Synthetic approved snapshot only; no public executable approval exists.
    await db.find_one_and_update(
        "object",
        {"id": approval.id},
        {
            "$set": {
                "context.status": "approved",
                "context.decision": "approved",
                "context.decider_id": "user-1",
                "context.decided_at": "2025-01-01T00:00:00Z",
            }
        },
    )
    return root, payload, db


@pytest.fixture
async def budget_root():
    return await approved_root(mandate_payload())


def request(payload, effect="model:1", cost="0.60", tool=False):
    route = payload["model_routes"][0]
    return MandateReservationRequest.model_validate(
        {
            "logical_effect_key": effect,
            "input_fingerprint": "a" * 64,
            "quote": {
                "provider": route["provider"],
                "model": route["model"],
                "credential_ref": route["credential_ref"],
                "quote_ref": "synthetic-quote",
                "valid_until": "2030-01-01T09:00:00Z",
                "upper_cost": cost,
            },
            **(
                {"capability_grant": payload["grants"][0]}
                if tool
                else {"model_route": route}
            ),
        }
    )


async def reserve(root, req, work_id=None):
    return await work_budgets.reserve_mandate_budget(
        work_item_id=work_id or root.work_item_id,
        principal_id="user-1",
        workspace_id="workspace-1",
        thread_id="thread-1",
        request=req,
    )


async def settle(record, cost, receipt="receipt-one"):
    return await work_budgets.settle_mandate_budget(
        reservation_id=record.reservation_id,
        principal_id="user-1",
        workspace_id="workspace-1",
        thread_id="thread-1",
        actual_cost=cost,
        receipt_ref=receipt,
    )


async def ledger(db, root):
    return (await db.get("object", root.id))["context"]["plan"].get("mandate_budget")


async def test_duplicate_reservation_and_settlement_account_once(budget_root):
    root, payload, db = budget_root
    req = request(payload)
    first = await reserve(root, req)
    same = await reserve(root, req)
    assert first.reservation_id == same.reservation_id
    assert (await ledger(db, root))["reserved_units"] == 60000000
    assert (await ledger(db, root))["model_requests"] == 1
    with pytest.raises(WorkError) as exc:
        await settle(first, None)
    assert exc.value.code == "work.cost_unavailable"
    assert (await ledger(db, root))["reserved_units"] == 60000000
    settled = await settle(first, Decimal("0.20"))
    assert settled.status == "settled"
    await settle(first, Decimal("0.20"))
    assert (await ledger(db, root))["charged_units"] == 20000000
    assert (await ledger(db, root))["reserved_units"] == 0
    assert (await ledger(db, root))["model_requests"] == 1
    with pytest.raises(WorkError) as exc:
        await settle(first, Decimal("0.30"))
    assert exc.value.code == "work.idempotency_conflict"


async def test_concurrent_children_share_one_spend_limit(budget_root):
    root, payload, db = budget_root
    children = []
    for _ in range(2):
        children.append(
            await work_items.enqueue_work_item(
                kind="capability",
                origin="mandate_child",
                principal_id="user-1",
                workspace_id="workspace-1",
                thread_id="thread-1",
                idempotency_key=uuid4().hex,
                parent_work_item_id=root.work_item_id,
                plan_revision=root.plan_revision,
                plan={"mandate_root_work_item_id": root.work_item_id},
                deadline_at="2030-01-01T09:00:00Z",
            )
        )
    results = await asyncio.gather(
        *(reserve(root, request(payload), child.work_item_id) for child in children),
        return_exceptions=True,
    )
    assert sum(not isinstance(result, Exception) for result in results) == 1
    error = next(result for result in results if isinstance(result, Exception))
    assert isinstance(error, WorkError) and error.code == "work.budget_exhausted"
    assert (await ledger(db, root))["reserved_units"] == 60000000
    assert (await ledger(db, root))["model_requests"] == 1
    winner = next(result for result in results if not isinstance(result, Exception))
    # A fresh graph context must reconcile the same committed reservation.
    from jvspatial.core.context import GraphContext, scoped_default_context_async

    async with scoped_default_context_async(GraphContext(db)):
        repeated = await reserve(root, request(payload), winner.work_item_id)
    assert repeated.id == winner.id
    assert (await ledger(db, root))["reserved_units"] == 60000000


async def test_unknown_price_expired_quote_and_changed_operation_fail_closed(
    budget_root,
):
    root, payload, db = budget_root
    with pytest.raises(WorkError) as exc:
        await reserve(root, request(payload, cost=None))
    assert exc.value.code == "work.cost_unavailable"
    assert await ledger(db, root) is None
    req = request(payload)
    expired = req.model_dump(mode="json")
    expired["quote"]["valid_until"] = "2000-01-01T00:00:00Z"
    with pytest.raises(WorkError) as exc:
        await reserve(root, MandateReservationRequest.model_validate(expired))
    assert exc.value.code == "work.cost_quote_expired"
    assert await ledger(db, root) is None
    await reserve(root, req)
    changed = req.model_dump(mode="json")
    changed["input_fingerprint"] = "b" * 64
    with pytest.raises(WorkError) as exc:
        await reserve(root, MandateReservationRequest.model_validate(changed))
    assert exc.value.code == "work.idempotency_conflict"
    assert (await ledger(db, root))["reserved_units"] == 60000000
    # Review retry must retain counters rather than conflict or reset them.
    repeated, _ = await _propose_review(payload)
    assert repeated.plan["mandate_budget"]["reserved_units"] == 60000000


async def test_per_capability_count_exhaustion_even_with_zero_quoted_cost(budget_root):
    root, payload, db = budget_root
    await reserve(root, request(payload, "tool:1", "0", tool=True))
    await reserve(root, request(payload, "tool:2", "0", tool=True))
    with pytest.raises(WorkError) as exc:
        await reserve(root, request(payload, "tool:3", "0", tool=True))
    assert exc.value.code == "work.budget_exhausted"
    assert (await ledger(db, root))["capability_calls"] == {"records.read": 2}
    assert (await ledger(db, root))["tool_calls"] == 2


async def test_settlement_after_cancellation_preserves_other_unknown_holds(budget_root):
    root, payload, db = budget_root
    first = await reserve(root, request(payload, "model:1", "0.40"))
    await reserve(root, request(payload, "model:2", "0.40"))
    await db.find_one_and_update(
        "object",
        {"id": root.id},
        {"$set": {"context.cancel_requested_at": "2026-01-01T00:00:00Z"}},
    )
    await settle(first, Decimal("0.10"))
    assert (await ledger(db, root))["charged_units"] == 10000000
    assert (await ledger(db, root))["reserved_units"] == 40000000
    with pytest.raises(WorkError) as exc:
        await reserve(root, request(payload, "model:3", "0.10"))
    assert exc.value.code == "work.mandate_inactive"


async def test_cost_overrun_records_usage_and_requests_stop(budget_root):
    root, payload, db = budget_root
    first = await reserve(root, request(payload, cost="0.40"))
    settled = await settle(first, Decimal("0.70"))
    assert settled.status == "overrun"
    doc = await db.get("object", root.id)
    assert doc["context"]["cancel_requested_at"]
    assert doc["context"]["plan"]["mandate_budget"]["charged_units"] == 70000000
    assert doc["context"]["plan"]["mandate_budget"]["reserved_units"] == 0
    stored = await db.get("object", first.id)
    assert stored["context"]["charged_units"] == 70000000


async def test_reservation_scope_and_model_attribution_are_exact(budget_root):
    root, payload, db = budget_root
    req = request(payload)
    with pytest.raises(WorkError) as exc:
        await work_budgets.reserve_mandate_budget(
            work_item_id=root.work_item_id,
            principal_id="other-user",
            workspace_id="workspace-1",
            thread_id="thread-1",
            request=req,
        )
    assert exc.value.code == "work.mandate_scope_denied"
    changed = req.model_dump(mode="json")
    changed["quote"]["credential_ref"] = "other-key"
    with pytest.raises(WorkError) as exc:
        await reserve(root, MandateReservationRequest.model_validate(changed))
    assert exc.value.code == "work.mandate_scope_denied"
    assert await ledger(db, root) is None


async def test_ledger_failure_rolls_back_reservation_fact(budget_root, monkeypatch):
    root, payload, db = budget_root
    req = request(payload)

    async def fail(*_args):
        raise RuntimeError("injected ledger failure")

    monkeypatch.setattr(work_budgets, "_write_ledger", fail)
    with pytest.raises(RuntimeError, match="injected ledger failure"):
        await reserve(root, req)
    reservation_id = hashlib.sha256(
        f"{root.work_item_id}:{root.work_item_id}:{req.logical_effect_key}".encode()
    ).hexdigest()
    assert await db.get("object", f"o.WorkBudgetReservation.{reservation_id}") is None
    assert await ledger(db, root) is None


async def test_model_count_blocks_before_new_reservation(budget_root):
    root, payload, db = budget_root
    for index in range(3):
        await reserve(root, request(payload, f"model:{index}", "0"))
    with pytest.raises(WorkError) as exc:
        await reserve(root, request(payload, "model:4", "0"))
    assert exc.value.code == "work.budget_exhausted"
    assert (await ledger(db, root))["model_requests"] == 3


async def test_internal_batch_reserves_actual_write_volume():
    payload = mandate_payload()
    payload["grants"][0]["operation"] = "internal_write"
    payload["limits"]["max_internal_writes"] = 2
    root, payload, db = await approved_root(payload)
    data = {
        "logical_effect_key": "write:1",
        "input_fingerprint": "a" * 64,
        "capability_grant": payload["grants"][0],
        "internal_write_units": 2,
        "quote": {
            "provider": "provider-1",
            "model": "model-1",
            "credential_ref": "key-ref-1",
            "quote_ref": "synthetic-quote",
            "valid_until": "2030-01-01T09:00:00Z",
            "upper_cost": "0",
        },
    }
    await reserve(root, MandateReservationRequest.model_validate(data))
    data["logical_effect_key"] = "write:2"
    data["internal_write_units"] = 1
    with pytest.raises(WorkError) as exc:
        await reserve(root, MandateReservationRequest.model_validate(data))
    assert exc.value.code == "work.budget_exhausted"
    assert (await ledger(db, root))["internal_writes"] == 2
    assert (await ledger(db, root))["tool_calls"] == 1


async def test_external_batch_respects_destination_effect_limit():
    payload = mandate_payload()
    payload["grants"][0]["operation"] = "external_effect"
    payload["limits"]["max_external_effects"] = 3
    payload["external_grants"] = [
        {
            "capability_key": "records.read",
            "provider": "provider-1",
            "credential_ref": "key-ref-1",
            "operation": "deliver",
            "destination": "reviewed-destination",
            "max_effects": 2,
        }
    ]
    root, payload, db = await approved_root(payload)
    data = {
        "logical_effect_key": "external:1",
        "input_fingerprint": "a" * 64,
        "capability_grant": payload["grants"][0],
        "external_grant": payload["external_grants"][0],
        "external_effect_units": 2,
        "quote": {
            "provider": "provider-1",
            "model": "model-1",
            "credential_ref": "key-ref-1",
            "quote_ref": "synthetic-quote",
            "valid_until": "2030-01-01T09:00:00Z",
            "upper_cost": "0",
        },
    }
    await reserve(root, MandateReservationRequest.model_validate(data))
    data["logical_effect_key"] = "external:2"
    data["external_effect_units"] = 1
    with pytest.raises(WorkError) as exc:
        await reserve(root, MandateReservationRequest.model_validate(data))
    assert exc.value.code == "work.budget_exhausted"
    assert (await ledger(db, root))["external_effects"] == 2
    assert (await ledger(db, root))["external_effects_by_capability"] == {
        "records.read": 2
    }


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("upper_units", 1, "work.budget_invalid"),
        ("request.input_fingerprint", "b" * 64, "work.budget_invalid"),
        ("reservation_id", "wrong-identity", "work.budget_invalid"),
        ("principal_id", "other-user", "work.mandate_scope_denied"),
    ],
)
async def test_tampered_reservation_cannot_replay_or_settle(
    budget_root, field, value, code
):
    root, payload, db = budget_root
    req = request(payload)
    record = await reserve(root, req)
    baseline = await ledger(db, root)
    await db.find_one_and_update(
        "object", {"id": record.id}, {"$set": {f"context.{field}": value}}
    )
    for action in (lambda: reserve(root, req), lambda: settle(record, Decimal("0.20"))):
        with pytest.raises(WorkError) as exc:
            await action()
        assert exc.value.code == code
    assert await ledger(db, root) == baseline


async def test_coherent_forged_request_cannot_replay_unreviewed_route(budget_root):
    root, payload, db = budget_root
    record = await reserve(root, request(payload))
    baseline = await ledger(db, root)
    data = record.request.copy()
    data["model_route"] = {**data["model_route"], "model": "unreviewed-model"}
    data["quote"] = {**data["quote"], "model": "unreviewed-model"}
    forged = MandateReservationRequest.model_validate(data)
    await db.find_one_and_update(
        "object",
        {"id": record.id},
        {
            "$set": {
                "context.request": forged.model_dump(mode="json"),
                "context.request_fingerprint": forged.fingerprint(),
            }
        },
    )
    with pytest.raises(WorkError) as exc:
        await reserve(root, forged)
    assert exc.value.code == "work.mandate_scope_denied"
    assert await ledger(db, root) == baseline


async def test_replay_rejects_missing_cost_hold(budget_root):
    root, payload, db = budget_root
    req = request(payload)
    await reserve(root, req)
    await db.find_one_and_update(
        "object",
        {"id": root.id},
        {"$set": {"context.plan.mandate_budget.reserved_units": 0}},
    )
    with pytest.raises(WorkError) as exc:
        await reserve(root, req)
    assert exc.value.code == "work.budget_invalid"


@pytest.mark.parametrize("global_limit", [10, 3])
async def test_two_destinations_have_independent_counts_and_shared_global_limit(
    global_limit,
):
    payload = mandate_payload()
    payload["grants"][0].update(operation="external_effect", max_calls=10)
    payload["limits"].update(max_external_effects=global_limit, max_tool_calls=10)
    payload["external_grants"] = [
        {
            "capability_key": "records.read",
            "provider": "provider-1",
            "credential_ref": "key-ref-1",
            "operation": "deliver",
            "destination": destination,
            "max_effects": 2,
        }
        for destination in ("destination-a", "destination-b")
    ]
    root, payload, db = await approved_root(payload)

    # Build exact external scope before validating its required effect volume.
    def scoped(index, units=2, effect=None):
        route = payload["model_routes"][0]
        return MandateReservationRequest.model_validate(
            {
                "logical_effect_key": effect or f"destination:{index}",
                "input_fingerprint": "a" * 64,
                "capability_grant": payload["grants"][0],
                "external_grant": payload["external_grants"][index],
                "external_effect_units": units,
                "quote": {
                    **{k: route[k] for k in ("provider", "model", "credential_ref")},
                    "quote_ref": "synthetic-quote",
                    "valid_until": "2030-01-01T09:00:00Z",
                    "upper_cost": "0",
                },
            }
        )

    await reserve(root, scoped(0))
    if global_limit == 3:
        with pytest.raises(WorkError) as exc:
            await reserve(root, scoped(1))
        assert exc.value.code == "work.budget_exhausted"
        assert (await ledger(db, root))["external_effects"] == 2
        return
    await reserve(root, scoped(1))
    await reserve(root, scoped(1))
    saved = await ledger(db, root)
    assert saved["external_effects"] == 4
    assert saved["external_effects_by_capability"] == {"records.read": 4}
    assert sorted(saved["external_effects_by_grant"].values()) == [2, 2]
    with pytest.raises(WorkError) as exc:
        await reserve(root, scoped(0, 1, "extra:first-destination"))
    assert exc.value.code == "work.budget_exhausted"
    assert await ledger(db, root) == saved


@pytest.mark.parametrize("external_count", [0, 1])
async def test_legacy_external_attribution_is_not_guessed(budget_root, external_count):
    root, payload, db = budget_root
    await reserve(root, request(payload, "before-upgrade", "0"))
    saved = await ledger(db, root)
    del saved["external_effects_by_grant"]
    saved["external_effects"] = external_count
    await db.find_one_and_update(
        "object", {"id": root.id}, {"$set": {"context.plan.mandate_budget": saved}}
    )
    if external_count:
        with pytest.raises(WorkError) as exc:
            await reserve(root, request(payload, "after-upgrade", "0"))
        assert exc.value.code == "work.budget_attribution_required"
        assert await ledger(db, root) == saved
    else:
        await reserve(root, request(payload, "after-upgrade", "0"))
        assert (await ledger(db, root))["external_effects_by_grant"] == {}


@pytest.fixture
async def leased_budget_child(budget_root):
    from app.agentive.services.work_execution import build_work_execution_context

    root, payload, db = budget_root
    parent_id = root.work_item_id
    children = []
    for _ in range(2):
        child = await work_items.enqueue_work_item(
            kind="capability",
            origin="mandate_child",
            principal_id="user-1",
            workspace_id="workspace-1",
            thread_id="thread-1",
            idempotency_key=uuid4().hex,
            parent_work_item_id=parent_id,
            plan_revision=root.plan_revision,
            plan={"mandate_root_work_item_id": root.work_item_id},
            deadline_at="2030-01-01T09:00:00Z",
        )
        children.append(child)
        parent_id = child.work_item_id
    leaf = await work_items.claim_due_candidate(
        worker_id="budget-fence-test",
        work_item_id=children[-1].work_item_id,
        lease_seconds=60,
    )
    assert leaf is not None
    context = build_work_execution_context(
        work_item=leaf, logical_step_key="provider:0"
    )
    data = request(payload, cost="0.10").model_dump(mode="json")
    data["logical_effect_key"] = context.effect_key
    req = MandateReservationRequest.model_validate(data)
    return root, children[0], leaf, context, req, db


async def reserve_leased(leaf, context, req):
    return await work_budgets.reserve_mandate_budget(
        work_item_id=leaf.work_item_id,
        principal_id="user-1",
        workspace_id="workspace-1",
        thread_id="thread-1",
        request=req,
        execution_context=context,
    )


async def test_current_leaf_lease_reserves_and_replays_once(leased_budget_child):
    root, parent, leaf, context, req, db = leased_budget_child
    first = await reserve_leased(leaf, context, req)
    repeated = await reserve_leased(leaf, context, req)
    assert repeated.id == first.id
    assert (await ledger(db, root))["reserved_units"] == 10000000
    assert (await ledger(db, root))["model_requests"] == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("lease_fence", 2),
        ("lease_token", "replacement-lease"),
        ("attempt", 2),
        ("lease_expires_at", "2000-01-01T00:00:00Z"),
        ("lease_expires_at", "2030-01-01T00:00:00"),
        ("lease_expires_at", "invalid-expiry"),
    ],
)
async def test_stale_or_invalid_leaf_lease_cannot_replay_hold(
    leased_budget_child, field, value
):
    root, parent, leaf, context, req, db = leased_budget_child
    await reserve_leased(leaf, context, req)
    baseline = await ledger(db, root)
    # Mutate the durable row while the claimed WorkItem remains cached.
    await db.find_one_and_update(
        "object", {"id": leaf.id}, {"$set": {f"context.{field}": value}}
    )
    with pytest.raises(WorkError) as exc:
        await reserve_leased(leaf, context, req)
    assert exc.value.code == "work.lease_lost"
    assert await ledger(db, root) == baseline


@pytest.mark.parametrize("ancestor", ["root", "parent"])
async def test_cancelled_ancestor_blocks_child_before_hold(
    leased_budget_child, ancestor
):
    root, parent, leaf, context, req, db = leased_budget_child
    selected = root if ancestor == "root" else parent
    await db.find_one_and_update(
        "object",
        {"id": selected.id},
        {"$set": {"context.cancel_requested_at": "2026-01-01T00:00:00Z"}},
    )
    with pytest.raises(WorkError) as exc:
        await reserve_leased(leaf, context, req)
    assert exc.value.code == "work.mandate_inactive"
    assert await ledger(db, root) is None


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("thread_id", "wrong-thread", "work.mandate_scope_denied"),
        ("run_id", "wrong-run", "work.lease_lost"),
        ("effect_key", "wrong-effect", "work.logical_step_conflict"),
        ("deadline_at", "2030-01-01T08:00:00Z", "work.lease_lost"),
        ("cancellation_signal", True, "work.cancelled"),
    ],
)
async def test_forged_execution_context_cannot_reserve(
    leased_budget_child, field, value, code
):
    root, parent, leaf, context, req, db = leased_budget_child
    changed = context.model_copy(update={field: value})
    with pytest.raises(WorkError) as exc:
        await reserve_leased(leaf, changed, req)
    assert exc.value.code == code
    assert await ledger(db, root) is None


async def test_logical_reservation_must_match_context_effect(leased_budget_child):
    root, parent, leaf, context, req, db = leased_budget_child
    data = req.model_dump(mode="json")
    data["logical_effect_key"] = "unrelated-logical-slot"
    with pytest.raises(WorkError) as exc:
        await reserve_leased(
            leaf, context, MandateReservationRequest.model_validate(data)
        )
    assert exc.value.code == "work.logical_step_conflict"
    assert await ledger(db, root) is None


@pytest.mark.parametrize("ancestor", ["root", "parent", "leaf"])
async def test_cancellation_serializes_with_leased_budget_transaction(
    leased_budget_child, monkeypatch, ancestor
):
    root, parent, leaf, context, req, db = leased_budget_child
    entered = asyncio.Event()
    release = asyncio.Event()
    original = work_budgets._write_ledger

    async def hold_transaction(database, locked_root, fields):
        entered.set()
        await asyncio.wait_for(release.wait(), timeout=5)
        await original(database, locked_root, fields)

    monkeypatch.setattr(work_budgets, "_write_ledger", hold_transaction)
    selected = {"root": root, "parent": parent, "leaf": leaf}[ancestor]
    holder = asyncio.create_task(reserve_leased(leaf, context, req))
    await asyncio.wait_for(entered.wait(), timeout=5)
    cancellation = asyncio.create_task(
        work_items.cancel_work_item(selected.work_item_id)
    )
    try:
        # Cancellation cannot mutate any locked ancestor before accounting
        # commits. Shield keeps the genuine cancellation alive after observing.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(cancellation), timeout=0.1)
    finally:
        release.set()
        await asyncio.gather(holder, cancellation)
    assert (await ledger(db, root))["reserved_units"] == 10000000
    with pytest.raises(WorkError) as exc:
        await reserve_leased(leaf, context, req)
    assert exc.value.code == "work.mandate_inactive"
    assert (await ledger(db, root))["reserved_units"] == 10000000


async def mark_dispatch(record, context, dispatch_ref="synthetic-dispatch-one"):
    return await work_budgets.mark_mandate_dispatch_intent(
        reservation_id=record.reservation_id,
        execution_context=context,
        request_fingerprint=record.request_fingerprint,
        dispatch_ref=dispatch_ref,
    )


async def reconcile_dispatch(
    record, outcome, evidence="synthetic-outcome", dispatch_ref=""
):
    return await work_budgets.reconcile_mandate_dispatch(
        reservation_id=record.reservation_id,
        principal_id="user-1",
        workspace_id="workspace-1",
        thread_id="thread-1",
        outcome=outcome,
        evidence_ref=evidence,
        dispatch_ref=dispatch_ref,
    )


async def test_dispatch_intent_persists_and_fresh_context_cannot_repeat(
    leased_budget_child,
):
    from jvspatial.core.context import GraphContext, scoped_default_context_async

    root, parent, leaf, context, req, db = leased_budget_child
    record = await reserve_leased(leaf, context, req)
    marked = await mark_dispatch(record, context)
    assert marked.dispatch_state == "intent" and marked.dispatched_at
    async with scoped_default_context_async(GraphContext(db)):
        with pytest.raises(WorkError) as exc:
            await mark_dispatch(record, context)
    assert exc.value.code == "work.outcome_reconciliation_required"
    assert (await ledger(db, root))["reserved_units"] == 10000000
    unknown = await reconcile_dispatch(
        marked, "unknown", dispatch_ref=marked.dispatch_ref
    )
    assert unknown.dispatch_state == "unknown"
    assert (
        await reconcile_dispatch(marked, "unknown", dispatch_ref=marked.dispatch_ref)
    ).id == marked.id
    with pytest.raises(WorkError) as exc:
        await reconcile_dispatch(marked, "not_dispatched")
    assert exc.value.code == "work.outcome_reconciliation_required"
    settled = await settle(marked, Decimal("0.04"), "synthetic-terminal-usage")
    assert settled.dispatch_state == "completed"
    assert settled.outcome_ref == settled.receipt_ref == "synthetic-terminal-usage"
    assert (await ledger(db, root))["reserved_units"] == 0
    assert (await ledger(db, root))["charged_units"] == 4000000
    with pytest.raises(WorkError) as exc:
        await mark_dispatch(record, context)
    assert exc.value.code == "work.outcome_reconciliation_required"


async def test_unmarked_hold_releases_after_cancel_without_refunding_volume(
    leased_budget_child,
):
    root, parent, leaf, context, req, db = leased_budget_child
    record = await reserve_leased(leaf, context, req)
    await work_items.cancel_work_item(leaf.work_item_id)
    released = await reconcile_dispatch(record, "not_dispatched", "never-started")
    assert released.status == "released" and released.dispatch_state == "not_dispatched"
    assert released.charged_units == 0
    assert (await ledger(db, root))["reserved_units"] == 0
    assert (await ledger(db, root))["model_requests"] == 1
    assert (
        await reconcile_dispatch(record, "not_dispatched", "never-started")
    ).id == record.id
    with pytest.raises(WorkError) as exc:
        await settle(record, Decimal("0"))
    assert exc.value.code == "work.reservation_released"
    with pytest.raises(WorkError) as exc:
        await reconcile_dispatch(record, "not_dispatched", "different-proof")
    assert exc.value.code == "work.idempotency_conflict"


async def test_released_logical_effect_cannot_reserve_again(leased_budget_child):
    root, parent, leaf, context, req, db = leased_budget_child
    record = await reserve_leased(leaf, context, req)
    await reconcile_dispatch(record, "not_dispatched")
    with pytest.raises(WorkError) as exc:
        await reserve_leased(leaf, context, req)
    assert exc.value.code == "work.reservation_released"
    assert (await ledger(db, root))["model_requests"] == 1


async def test_legacy_dispatch_history_cannot_release_or_mark(leased_budget_child):
    root, parent, leaf, context, req, db = leased_budget_child
    record = await reserve_leased(leaf, context, req)
    stored = await db.get("object", record.id)
    for key in ("dispatch_state", "dispatch_ref", "dispatched_at", "outcome_ref"):
        stored["context"].pop(key, None)
    await db.save("object", stored)
    for operation in (
        lambda: mark_dispatch(record, context),
        lambda: reconcile_dispatch(record, "not_dispatched"),
    ):
        with pytest.raises(WorkError) as exc:
            await operation()
        assert exc.value.code == "work.outcome_reconciliation_required"
    assert (await ledger(db, root))["reserved_units"] == 10000000


@pytest.mark.parametrize("ancestor", ["root", "parent", "leaf"])
async def test_cancel_before_dispatch_intent_retains_hold(
    leased_budget_child, ancestor
):
    root, parent, leaf, context, req, db = leased_budget_child
    record = await reserve_leased(leaf, context, req)
    await work_items.cancel_work_item(
        {"root": root, "parent": parent, "leaf": leaf}[ancestor].work_item_id
    )
    with pytest.raises(WorkError) as exc:
        await mark_dispatch(record, context)
    assert exc.value.code == "work.mandate_inactive"
    assert (await db.get("object", record.id))["context"][
        "dispatch_state"
    ] == "not_started"
    assert (await ledger(db, root))["reserved_units"] == 10000000


async def test_mark_and_release_race_has_only_one_winner(leased_budget_child):
    root, parent, leaf, context, req, db = leased_budget_child
    record = await reserve_leased(leaf, context, req)
    results = await asyncio.gather(
        mark_dispatch(record, context),
        reconcile_dispatch(record, "not_dispatched"),
        return_exceptions=True,
    )
    assert sum(not isinstance(r, Exception) for r in results) == 1
    stored = (await db.get("object", record.id))["context"]
    account = await ledger(db, root)
    if stored["status"] == "released":
        assert stored["dispatch_state"] == "not_dispatched"
        assert account["reserved_units"] == 0
    else:
        assert stored["dispatch_state"] == "intent"
        assert account["reserved_units"] == 10000000
    assert account["model_requests"] == 1


async def test_duplicate_dispatch_race_admits_only_one_intent(leased_budget_child):
    root, parent, leaf, context, req, db = leased_budget_child
    record = await reserve_leased(leaf, context, req)
    results = await asyncio.gather(
        mark_dispatch(record, context),
        mark_dispatch(record, context),
        return_exceptions=True,
    )
    assert sum(not isinstance(r, Exception) for r in results) == 1
    error = next(r for r in results if isinstance(r, Exception))
    assert (
        isinstance(error, WorkError)
        and error.code == "work.outcome_reconciliation_required"
    )
    assert (await ledger(db, root))["reserved_units"] == 10000000


@pytest.mark.parametrize("change", ["fingerprint", "thread", "lease", "expiry"])
async def test_dispatch_revalidates_request_scope_lease_quote(
    leased_budget_child, change
):
    root, parent, leaf, context, req, db = leased_budget_child
    record = await reserve_leased(leaf, context, req)
    if change == "fingerprint":
        record.request_fingerprint = "b" * 64
        code = "work.idempotency_conflict"
    elif change == "thread":
        context = context.model_copy(update={"thread_id": "other-thread"})
        code = "work.mandate_scope_denied"
    elif change == "lease":
        await db.find_one_and_update(
            "object", {"id": leaf.id}, {"$set": {"context.lease_fence": 2}}
        )
        code = "work.lease_lost"
    else:
        data = req.model_dump(mode="json")
        data["quote"]["valid_until"] = "2000-01-01T00:00:00Z"
        expired = MandateReservationRequest.model_validate(data)
        record.request_fingerprint = expired.fingerprint()
        await db.find_one_and_update(
            "object",
            {"id": record.id},
            {
                "$set": {
                    "context.request": data,
                    "context.request_fingerprint": expired.fingerprint(),
                }
            },
        )
        code = "work.cost_quote_expired"
    with pytest.raises(WorkError) as exc:
        await mark_dispatch(record, context)
    assert exc.value.code == code
    assert (await ledger(db, root))["reserved_units"] == 10000000


@pytest.mark.parametrize(
    "outcome,ref,dispatch,code",
    [
        ("completed", "proof", "", "work.budget_invalid"),
        ("unknown", "proof", "wrong-dispatch", "work.idempotency_conflict"),
        (
            "not_dispatched",
            "proof",
            "synthetic-dispatch-one",
            "work.outcome_reconciliation_required",
        ),
        ("unknown", " ", "synthetic-dispatch-one", "work.budget_receipt_required"),
    ],
)
async def test_unknown_outcome_cannot_fabricate_release(
    leased_budget_child, outcome, ref, dispatch, code
):
    root, parent, leaf, context, req, db = leased_budget_child
    record = await reserve_leased(leaf, context, req)
    await mark_dispatch(record, context)
    with pytest.raises(WorkError) as exc:
        await reconcile_dispatch(record, outcome, ref, dispatch)
    assert exc.value.code == code
    assert (await ledger(db, root))["reserved_units"] == 10000000


@pytest.mark.parametrize(
    "fields",
    [
        {"dispatch_ref": ""},
        {"dispatch_ref": " marker "},
        {"dispatched_at": "garbage"},
        {"dispatched_at": "2030-01-01T00:00:00"},
        {"dispatch_state": "not_started"},
        {"outcome_ref": "made-up-outcome"},
    ],
)
async def test_corrupt_dispatch_record_cannot_release_or_settle(
    leased_budget_child, fields
):
    root, parent, leaf, context, req, db = leased_budget_child
    record = await reserve_leased(leaf, context, req)
    await mark_dispatch(record, context)
    await db.find_one_and_update(
        "object",
        {"id": record.id},
        {"$set": {f"context.{key}": value for key, value in fields.items()}},
    )
    for operation in (
        lambda: reconcile_dispatch(record, "not_dispatched"),
        lambda: settle(record, Decimal("0.02")),
    ):
        with pytest.raises(WorkError) as exc:
            await operation()
        assert exc.value.code == "work.budget_invalid"
    assert (await ledger(db, root))["reserved_units"] == 10000000


async def test_release_rolls_back_if_record_transition_fails(
    leased_budget_child, monkeypatch
):
    root, parent, leaf, context, req, db = leased_budget_child
    record = await reserve_leased(leaf, context, req)
    from jvspatial.core.context import graph_transaction

    async with graph_transaction(database=db) as graph:
        cls = type(graph.database)
    original = cls.find_one_and_update

    async def fail_release(self, collection, query, update, **kwargs):
        if (
            query.get("id") == record.id
            and update.get("$set", {}).get("context.status") == "released"
        ):
            return None
        return await original(self, collection, query, update, **kwargs)

    monkeypatch.setattr(cls, "find_one_and_update", fail_release)
    with pytest.raises(WorkError) as exc:
        await reconcile_dispatch(record, "not_dispatched")
    assert exc.value.code == "work.cas_conflict"
    assert (await ledger(db, root))["reserved_units"] == 10000000
    assert (await db.get("object", record.id))["context"]["status"] == "reserved"


@pytest.mark.parametrize("ancestor", ["root", "parent", "leaf"])
async def test_cancel_serializes_with_dispatch_intent(
    leased_budget_child, monkeypatch, ancestor
):
    from jvspatial.core.context import graph_transaction

    root, parent, leaf, context, req, db = leased_budget_child
    record = await reserve_leased(leaf, context, req)
    async with graph_transaction(database=db) as graph:
        cls = type(graph.database)
    original = cls.find_one_and_update
    entered = asyncio.Event()
    release = asyncio.Event()

    async def hold_marker(self, collection, query, update, **kwargs):
        if (
            query.get("id") == record.id
            and update.get("$set", {}).get("context.dispatch_state") == "intent"
        ):
            entered.set()
            await release.wait()
        return await original(self, collection, query, update, **kwargs)

    monkeypatch.setattr(cls, "find_one_and_update", hold_marker)
    marking = asyncio.create_task(mark_dispatch(record, context))
    cancelling = None
    try:
        await asyncio.wait_for(entered.wait(), 3)
        target = {"root": root, "parent": parent, "leaf": leaf}[ancestor]
        cancelling = asyncio.create_task(
            work_items.cancel_work_item(target.work_item_id)
        )
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(cancelling), 0.1)
        release.set()
        marked = await asyncio.wait_for(marking, 3)
        await asyncio.wait_for(cancelling, 3)
        assert marked.dispatch_state == "intent"
        with pytest.raises(WorkError):
            await mark_dispatch(record, context)
        with pytest.raises(WorkError) as exc:
            await reconcile_dispatch(record, "not_dispatched")
        assert exc.value.code == "work.outcome_reconciliation_required"
        assert (await ledger(db, root))["reserved_units"] == 10000000
    finally:
        release.set()
        await asyncio.gather(
            marking, *([cancelling] if cancelling else []), return_exceptions=True
        )


async def test_leased_hold_needs_intent_before_terminal_usage(leased_budget_child):
    root, parent, leaf, context, req, db = leased_budget_child
    record = await reserve_leased(leaf, context, req)
    with pytest.raises(WorkError) as exc:
        await settle(record, Decimal("0.01"))
    assert exc.value.code == "work.dispatch_intent_required"
    assert (await ledger(db, root))["reserved_units"] == 10000000


async def test_accounting_only_history_does_not_prove_no_dispatch(budget_root):
    root, payload, db = budget_root
    record = await reserve(root, request(payload))
    assert record.dispatch_state == "legacy_untracked"
    with pytest.raises(WorkError) as exc:
        await reconcile_dispatch(record, "not_dispatched")
    assert exc.value.code == "work.outcome_reconciliation_required"
    assert (await ledger(db, root))["reserved_units"] == 60000000
