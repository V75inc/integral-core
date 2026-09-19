"""Work kernel recovery pass (Task 4 / I-WORK-05)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.agentive.services import work_items, work_recovery
from app.agentive.work_models import WorkItem, WorkOutboxEntry


@pytest.mark.asyncio
async def test_recovery_expires_overdue_queued_items() -> None:
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    item = await work_items.enqueue_work_item(
        kind="capability",
        origin="http",
        principal_id="rec-u-1",
        workspace_id="rec-ws-1",
        idempotency_key="rec-exp-1",
        input_payload={"capability_key": "a"},
        deadline_at=past,
    )
    report = await work_recovery.run_recovery_pass()
    assert report.expired >= 1
    loaded = await WorkItem.get(item.id)
    assert loaded is not None
    assert loaded.status == "expired"


@pytest.mark.asyncio
async def test_recovery_reclaims_expired_running_leases() -> None:
    item = await work_items.enqueue_work_item(
        kind="capability",
        origin="http",
        principal_id="rec-u-2",
        workspace_id="rec-ws-2",
        idempotency_key="rec-lease-1",
        input_payload={"capability_key": "a"},
    )
    claimed = await work_items.claim_due_candidate(
        worker_id="old-worker",
        work_item_id=item.work_item_id,
        lease_seconds=1,
    )
    assert claimed is not None
    prior_fence = int(claimed.lease_fence)
    await work_items.force_expire_lease_for_tests(claimed.work_item_id)
    report = await work_recovery.run_recovery_pass(reclaim_worker_id="recovery")
    assert report.reclaimed >= 1
    loaded = await WorkItem.get(item.id)
    assert loaded is not None
    assert loaded.status == "running"
    assert loaded.lease_owner == "recovery"
    assert loaded.lease_fence == prior_fence + 1


@pytest.mark.asyncio
async def test_recovery_backfills_missing_outbox_and_is_idempotent() -> None:
    object_id = work_items.work_item_object_id(
        kind="capability",
        origin="http",
        principal_id="rec-u-3",
        workspace_id="rec-ws-3",
        idempotency_key="rec-outbox-1",
    )
    work_item_id = object_id.removeprefix("o.WorkItem.")
    item, created = await WorkItem.create_if_absent(
        id=object_id,
        work_item_id=work_item_id,
        kind="capability",
        origin="http",
        principal_id="rec-u-3",
        workspace_id="rec-ws-3",
        idempotency_key="rec-outbox-1",
        input_payload={"capability_key": "a"},
        input_fingerprint=work_items.input_fingerprint({"capability_key": "a"}),
        status="queued",
        attempt=0,
        transition_seq=0,
        next_attempt_at="2026-01-01T00:00:00+00:00",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )
    assert created is True
    assert list(
        await WorkOutboxEntry.find({"context.work_item_id": item.work_item_id})
    ) == []
    first = await work_recovery.run_recovery_pass()
    assert first.outbox_backfilled >= 1
    second = await work_recovery.run_recovery_pass()
    assert second.expired == 0
    assert second.reclaimed == 0
    assert second.outbox_backfilled == 0
    assert second.retry_waited == 0
    assert second.terminalized == 0


@pytest.mark.asyncio
async def test_recovery_never_reopens_terminal_work() -> None:
    item = await work_items.enqueue_work_item(
        kind="capability",
        origin="http",
        principal_id="rec-u-4",
        workspace_id="rec-ws-4",
        idempotency_key="rec-term-1",
        input_payload={"capability_key": "a"},
    )
    cancelled = await work_items.cancel_work_item(item.work_item_id)
    assert cancelled.status == "cancelled"
    await work_recovery.run_recovery_pass()
    loaded = await WorkItem.get(item.id)
    assert loaded is not None
    assert loaded.status == "cancelled"
