"""WorkItem retry, deadline, and cancellation controls (Task 4)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.agentive.services import work_items
from app.agentive.work_models import WorkItem
from app.schemas.agentive.work import RetryPolicy, WorkError, WorkFailure


def test_failure_classes_and_retry_policy_surface() -> None:
    failure = WorkFailure.model_validate(
        {
            "class": "transient",
            "code": "work.upstream_timeout",
            "message": "timeout",
            "retryable": True,
        }
    )
    assert failure.class_ == "transient"
    assert failure.retryable is True
    for cls_name in (
        "transient",
        "rate_limited",
        "dependency_unavailable",
        "permanent",
        "policy_denied",
        "cancelled",
        "deadline_exceeded",
        "non_replayable",
    ):
        WorkFailure.model_validate(
            {
                "class": cls_name,
                "code": f"work.{cls_name}",
                "message": cls_name,
                "retryable": cls_name
                in {"transient", "rate_limited", "dependency_unavailable"},
            }
        )


def test_retry_backoff_is_deterministic() -> None:
    policy = RetryPolicy(
        max_attempts=5,
        base_delay_seconds=1.0,
        max_delay_seconds=60.0,
        jitter_ratio=0.1,
    )
    d1 = work_items.compute_retry_delay(
        work_item_id="abc", attempt=1, retry_policy=policy
    )
    d2 = work_items.compute_retry_delay(
        work_item_id="abc", attempt=1, retry_policy=policy
    )
    d3 = work_items.compute_retry_delay(
        work_item_id="abc", attempt=2, retry_policy=policy
    )
    assert d1 == d2
    assert d3 != d1
    assert 0.9 <= d1 <= 1.1
    assert 1.8 <= d3 <= 2.2


@pytest.mark.asyncio
async def test_schedule_retry_moves_to_retry_wait() -> None:
    item = await work_items.enqueue_work_item(
        kind="capability",
        origin="http",
        principal_id="ctl-u-1",
        workspace_id="ctl-ws-1",
        idempotency_key="retry-1",
        input_payload={"capability_key": "a"},
        retry_policy=RetryPolicy(max_attempts=3, base_delay_seconds=1.0),
    )
    claimed = await work_items.claim_due_candidate(
        worker_id="w1", work_item_id=item.work_item_id, lease_seconds=30
    )
    assert claimed is not None
    waiting = await work_items.schedule_retry(
        claimed.work_item_id,
        lease_token=claimed.lease_token,
        lease_fence=claimed.lease_fence,
        failure=WorkFailure(
            class_="transient",
            code="work.upstream_timeout",
            message="timeout",
            retryable=True,
        ),
    )
    assert waiting.status == "retry_wait"
    assert waiting.failure is not None
    assert waiting.failure["class"] == "transient"
    assert waiting.next_attempt_at > waiting.updated_at


@pytest.mark.asyncio
async def test_exhausted_retries_go_dead_letter() -> None:
    item = await work_items.enqueue_work_item(
        kind="capability",
        origin="http",
        principal_id="ctl-u-2",
        workspace_id="ctl-ws-2",
        idempotency_key="dead-1",
        input_payload={"capability_key": "a"},
        retry_policy=RetryPolicy(max_attempts=1, base_delay_seconds=1.0),
    )
    claimed = await work_items.claim_due_candidate(
        worker_id="w1", work_item_id=item.work_item_id, lease_seconds=30
    )
    assert claimed is not None
    dead = await work_items.schedule_retry(
        claimed.work_item_id,
        lease_token=claimed.lease_token,
        lease_fence=claimed.lease_fence,
        failure=WorkFailure(
            class_="transient",
            code="work.upstream_timeout",
            message="timeout",
            retryable=True,
        ),
    )
    assert dead.status == "dead_letter"


@pytest.mark.asyncio
async def test_deadline_blocks_claim_and_completion() -> None:
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    item = await work_items.enqueue_work_item(
        kind="capability",
        origin="http",
        principal_id="ctl-u-3",
        workspace_id="ctl-ws-3",
        idempotency_key="dl-1",
        input_payload={"capability_key": "a"},
        deadline_at=past,
    )
    claimed = await work_items.claim_due_candidate(
        worker_id="w1", work_item_id=item.work_item_id, lease_seconds=30
    )
    assert claimed is None
    loaded = await WorkItem.get(item.id)
    assert loaded is not None
    assert loaded.status == "expired"


@pytest.mark.asyncio
async def test_cancel_queued_terminalizes_immediately() -> None:
    item = await work_items.enqueue_work_item(
        kind="capability",
        origin="http",
        principal_id="ctl-u-4",
        workspace_id="ctl-ws-4",
        idempotency_key="cancel-q",
        input_payload={"capability_key": "a"},
    )
    cancelled = await work_items.cancel_work_item(item.work_item_id)
    assert cancelled.status == "cancelled"


@pytest.mark.asyncio
async def test_cancel_running_sets_request_and_blocks_completion() -> None:
    item = await work_items.enqueue_work_item(
        kind="capability",
        origin="http",
        principal_id="ctl-u-5",
        workspace_id="ctl-ws-5",
        idempotency_key="cancel-r",
        input_payload={"capability_key": "a"},
    )
    claimed = await work_items.claim_due_candidate(
        worker_id="w1", work_item_id=item.work_item_id, lease_seconds=30
    )
    assert claimed is not None
    requested = await work_items.cancel_work_item(claimed.work_item_id)
    assert requested.status == "running"
    assert requested.cancel_requested_at
    with pytest.raises(WorkError) as exc:
        await work_items.transition_leased(
            claimed.work_item_id,
            lease_token=claimed.lease_token,
            lease_fence=claimed.lease_fence,
            expected_status="running",
            target="succeeded",
        )
    assert exc.value.code == "work.cancelled"
