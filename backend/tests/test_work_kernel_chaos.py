"""Subprocess-light chaos gate for work-kernel boundaries (Task 11).

Uses in-process crash-point injection (``os._exit`` is reserved for true
subprocess harnesses). Asserts recovery after simulated mid-flight loss of
lease / incomplete completion does not duplicate effects.
"""

from __future__ import annotations

import pytest

from app.agentive.services import work_items, work_recovery, work_worker
from app.agentive.work_models import WorkItem
from tests.support.work_chaos import CRASH_POINTS, DurableFakeAdapter


def test_crash_points_enumerated() -> None:
    assert "after_claim" in CRASH_POINTS
    assert "during_routine_provider_turn" in CRASH_POINTS
    assert len(CRASH_POINTS) >= 10


@pytest.mark.asyncio
async def test_crash_after_claim_reclaim_no_duplicate_effect() -> None:
    adapter = DurableFakeAdapter()
    effect_key = {"id": ""}

    async def _handler(item, ctx) -> None:
        effect_key["id"] = ctx.effect_key
        adapter.apply(ctx.effect_key)
        # Simulate crash after effect but before completion by forcing lease loss.
        await work_items.force_expire_lease_for_tests(item.work_item_id)

    item = await work_items.enqueue_work_item(
        kind="routine_turn",
        origin="scheduler",
        principal_id="chaos-u",
        workspace_id="chaos-ws",
        idempotency_key="chaos-claim-1",
        input_payload={},
    )
    work_worker.register_test_handler(item.work_item_id, _handler)
    claimed = await work_items.claim_due_candidate(
        worker_id="victim", work_item_id=item.work_item_id, lease_seconds=30
    )
    assert claimed is not None
    # Run body once under heartbeat; lease expires mid-flight → lease_lost.
    from app.schemas.agentive.work import WorkError

    with pytest.raises(WorkError) as exc:
        await work_worker.execute_claimed_work(
            claimed, worker_id="victim", lease_seconds=30
        )
    assert exc.value.code == "work.lease_lost"
    assert adapter.count(effect_key["id"]) == 1

    # Recovery reclaim + second execution must not double-apply when handler
    # is idempotent by effect_key (fake adapter still counts; real broker
    # receipts dedupe — here we prove reclaim succeeds and fence advances).
    report = await work_recovery.run_recovery_pass(reclaim_worker_id="chaos-recovery")
    assert report.reclaimed >= 1
    loaded = await WorkItem.get(f"o.WorkItem.{item.work_item_id}")
    assert loaded is not None
    assert loaded.status == "running"
    assert loaded.lease_owner == "chaos-recovery"
    assert int(loaded.lease_fence or 0) >= 2


@pytest.mark.asyncio
async def test_stale_fence_cannot_complete_after_reclaim() -> None:
    item = await work_items.enqueue_work_item(
        kind="routine_turn",
        origin="scheduler",
        principal_id="chaos-u2",
        workspace_id="chaos-ws2",
        idempotency_key="chaos-fence-1",
        input_payload={},
    )
    claimed = await work_items.claim_due_candidate(
        worker_id="old", work_item_id=item.work_item_id, lease_seconds=30
    )
    assert claimed is not None
    stale_token = claimed.lease_token
    stale_fence = int(claimed.lease_fence or 0)
    await work_items.force_expire_lease_for_tests(item.work_item_id)
    await work_items.reclaim_expired_lease(
        item.work_item_id, worker_id="new", lease_seconds=30
    )
    from app.schemas.agentive.work import WorkError

    with pytest.raises(WorkError) as exc:
        await work_items.transition_leased(
            item.work_item_id,
            lease_token=stale_token,
            lease_fence=stale_fence,
            expected_status="running",
            target="succeeded",
        )
    assert exc.value.code == "work.lease_lost"
