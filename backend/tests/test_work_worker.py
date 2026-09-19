"""Static work worker handlers and supervised heartbeat (Task 6)."""

from __future__ import annotations

import asyncio

import pytest

from app.agentive.services import work_items, work_worker
from app.agentive.work_models import WorkItem
from app.schemas.agentive.work import WorkError


@pytest.fixture(autouse=True)
def _clear_handlers():
    work_worker.clear_test_handlers()
    work_worker.set_crash_point(None)
    yield
    work_worker.clear_test_handlers()
    work_worker.set_crash_point(None)


@pytest.mark.asyncio
async def test_unknown_kind_fails_permanently() -> None:
    item = await work_items.enqueue_work_item(
        kind="capability",
        origin="http",
        principal_id="ww-u-1",
        workspace_id="ww-ws-1",
        idempotency_key="ww-unk",
        input_payload={},
    )
    claimed = await work_items.claim_due_candidate(
        worker_id="w1", work_item_id=item.work_item_id, lease_seconds=30
    )
    assert claimed is not None
    claimed.kind = "not_a_kind"
    await claimed.save()
    done = await work_worker.execute_claimed_work(claimed, worker_id="w1")
    assert done.status == "failed"
    assert done.failure is not None
    assert done.failure["code"] == "work.unknown_kind"


@pytest.mark.asyncio
async def test_capability_requires_principal_and_workspace() -> None:
    item = await work_items.enqueue_work_item(
        kind="capability",
        origin="http",
        principal_id="ww-u-2",
        workspace_id="ww-ws-2",
        idempotency_key="ww-scope",
        input_payload={"capability_key": "integral_list_entries"},
    )
    claimed = await work_items.claim_due_candidate(
        worker_id="w1", work_item_id=item.work_item_id, lease_seconds=30
    )
    assert claimed is not None
    claimed.principal_id = ""
    await claimed.save()
    done = await work_worker.execute_claimed_work(claimed, worker_id="w1")
    assert done.status == "failed"
    assert done.failure["code"] == "work.policy_denied"


@pytest.mark.asyncio
async def test_routine_turn_runs_test_handler_under_lease() -> None:
    seen = {"n": 0}

    async def _handler(item, ctx) -> None:
        seen["n"] += 1
        assert ctx.lease_token == item.lease_token

    item = await work_items.enqueue_work_item(
        kind="routine_turn",
        origin="scheduler",
        principal_id="ww-u-3",
        workspace_id="ww-ws-3",
        idempotency_key="ww-rt",
        input_payload={"routine_id": "r1"},
    )
    work_worker.register_test_handler(item.work_item_id, _handler)
    done = await work_worker.process_one_due_item(
        worker_id="w1", work_item_id=item.work_item_id, lease_seconds=30
    )
    assert done is not None
    assert done.status == "succeeded"
    assert seen["n"] == 1


@pytest.mark.asyncio
async def test_supervised_heartbeat_during_long_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    heartbeats = {"n": 0}
    real_hb = work_items.heartbeat_lease

    async def _counting_hb(*args, **kwargs):
        heartbeats["n"] += 1
        return await real_hb(*args, **kwargs)

    async def _slow(item, ctx) -> None:
        await asyncio.sleep(0.35)

    item = await work_items.enqueue_work_item(
        kind="routine_turn",
        origin="scheduler",
        principal_id="ww-u-4",
        workspace_id="ww-ws-4",
        idempotency_key="ww-hb",
        input_payload={},
    )
    work_worker.register_test_handler(item.work_item_id, _slow)
    # lease=0.6 → heartbeat interval 0.2; sleep 0.35 → at least one beat
    monkeypatch.setattr(work_items, "heartbeat_lease", _counting_hb)
    done = await work_worker.process_one_due_item(
        worker_id="w1",
        work_item_id=item.work_item_id,
        lease_seconds=0.6,
    )
    assert done is not None
    assert done.status == "succeeded"
    assert heartbeats["n"] >= 1


@pytest.mark.asyncio
async def test_lease_loss_blocks_completion() -> None:
    async def _steal(item, ctx) -> None:
        await work_items.force_expire_lease_for_tests(item.work_item_id)
        await work_items.reclaim_expired_lease(
            item.work_item_id, worker_id="thief", lease_seconds=30
        )

    item = await work_items.enqueue_work_item(
        kind="routine_turn",
        origin="scheduler",
        principal_id="ww-u-5",
        workspace_id="ww-ws-5",
        idempotency_key="ww-steal",
        input_payload={},
    )
    work_worker.register_test_handler(item.work_item_id, _steal)
    claimed = await work_items.claim_due_candidate(
        worker_id="victim", work_item_id=item.work_item_id, lease_seconds=30
    )
    assert claimed is not None
    with pytest.raises(WorkError) as exc:
        await work_worker.execute_claimed_work(
            claimed, worker_id="victim", lease_seconds=30
        )
    assert exc.value.code == "work.lease_lost"
    loaded = await WorkItem.get(f"o.WorkItem.{item.work_item_id}")
    assert loaded is not None
    assert loaded.status == "running"
    assert loaded.lease_owner == "thief"
