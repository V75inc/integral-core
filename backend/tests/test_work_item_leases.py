"""WorkItem lease claim, heartbeat, fence, and reclaim (Task 3)."""

from __future__ import annotations

import asyncio

import pytest

from app.agentive.services import work_items
from app.schemas.agentive.work import WorkError


@pytest.mark.asyncio
async def test_claim_moves_queued_to_running_and_increments_attempt() -> None:
    item = await work_items.enqueue_work_item(
        kind="capability",
        origin="http",
        principal_id="lease-u-1",
        workspace_id="lease-ws-1",
        idempotency_key="claim-1",
        input_payload={"capability_key": "a"},
    )
    assert item.attempt == 0
    claimed = await work_items.claim_due_candidate(
        worker_id="worker-a",
        lease_seconds=30,
        work_item_id=item.work_item_id,
    )
    assert claimed is not None
    assert claimed.status == "running"
    assert claimed.attempt == 1
    assert claimed.lease_owner == "worker-a"
    assert claimed.lease_token
    assert claimed.lease_fence == 1
    assert claimed.lease_expires_at


@pytest.mark.asyncio
async def test_second_claim_rejected_while_lease_valid() -> None:
    item = await work_items.enqueue_work_item(
        kind="capability",
        origin="http",
        principal_id="lease-u-2",
        workspace_id="lease-ws-2",
        idempotency_key="claim-2",
        input_payload={"capability_key": "a"},
    )
    first = await work_items.claim_due_candidate(
        worker_id="worker-a",
        lease_seconds=60,
        work_item_id=item.work_item_id,
    )
    assert first is not None
    second = await work_items.claim_due_candidate(
        worker_id="worker-b",
        lease_seconds=60,
        work_item_id=item.work_item_id,
    )
    assert second is None
    assert first.lease_owner == "worker-a"


@pytest.mark.asyncio
async def test_heartbeat_renews_when_token_and_fence_match() -> None:
    item = await work_items.enqueue_work_item(
        kind="capability",
        origin="http",
        principal_id="lease-u-3",
        workspace_id="lease-ws-3",
        idempotency_key="hb-1",
        input_payload={"capability_key": "a"},
    )
    claimed = await work_items.claim_due_candidate(
        worker_id="worker-a",
        lease_seconds=30,
        work_item_id=item.work_item_id,
    )
    assert claimed is not None
    before = claimed.lease_expires_at
    assert work_items.recommended_heartbeat_interval(30) <= 30 / 3
    renewed = await work_items.heartbeat_lease(
        claimed.work_item_id,
        lease_token=claimed.lease_token,
        lease_fence=claimed.lease_fence,
        worker_id="worker-a",
        lease_seconds=30,
    )
    assert renewed.lease_expires_at >= before
    with pytest.raises(WorkError) as exc:
        await work_items.heartbeat_lease(
            claimed.work_item_id,
            lease_token="stale-token",
            lease_fence=claimed.lease_fence,
            worker_id="worker-a",
            lease_seconds=30,
        )
    assert exc.value.code == "work.lease_lost"


@pytest.mark.asyncio
async def test_stale_completion_rejected_after_reclaim() -> None:
    item = await work_items.enqueue_work_item(
        kind="capability",
        origin="http",
        principal_id="lease-u-4",
        workspace_id="lease-ws-4",
        idempotency_key="stale-1",
        input_payload={"capability_key": "a"},
    )
    claimed = await work_items.claim_due_candidate(
        worker_id="worker-a",
        lease_seconds=1,
        work_item_id=item.work_item_id,
    )
    assert claimed is not None
    stale_token = claimed.lease_token
    stale_fence = claimed.lease_fence

    # Force expiry then reclaim.
    await work_items.force_expire_lease_for_tests(claimed.work_item_id)
    reclaimed = await work_items.reclaim_expired_lease(
        claimed.work_item_id,
        worker_id="worker-b",
        lease_seconds=30,
    )
    assert reclaimed.lease_owner == "worker-b"
    assert reclaimed.lease_fence == stale_fence + 1
    assert reclaimed.lease_token != stale_token

    with pytest.raises(WorkError) as exc:
        await work_items.transition_leased(
            claimed.work_item_id,
            lease_token=stale_token,
            lease_fence=stale_fence,
            expected_status="running",
            target="succeeded",
        )
    assert exc.value.code == "work.lease_lost"

    done = await work_items.transition_leased(
        claimed.work_item_id,
        lease_token=reclaimed.lease_token,
        lease_fence=reclaimed.lease_fence,
        expected_status="running",
        target="succeeded",
    )
    assert done.status == "succeeded"


@pytest.mark.asyncio
async def test_dev_claim_serialized_by_process_lock() -> None:
    """Non-Postgres path serializes claims under the process lock."""
    item = await work_items.enqueue_work_item(
        kind="capability",
        origin="http",
        principal_id="lease-u-5",
        workspace_id="lease-ws-5",
        idempotency_key="lock-1",
        input_payload={"capability_key": "a"},
    )
    results = await asyncio.gather(
        work_items.claim_due_candidate(
            worker_id="w1", lease_seconds=30, work_item_id=item.work_item_id
        ),
        work_items.claim_due_candidate(
            worker_id="w2", lease_seconds=30, work_item_id=item.work_item_id
        ),
    )
    winners = [r for r in results if r is not None]
    assert len(winners) == 1
