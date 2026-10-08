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
