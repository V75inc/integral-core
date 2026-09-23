"""Durable fail-closed WorkApproval authority (Task 7)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.agentive.services import work_approvals, work_items
from app.agentive.work_models import WorkApproval, WorkItem
from app.schemas.agentive.work import WorkError


async def _running_item(**kwargs):
    item = await work_items.enqueue_work_item(
        kind="capability",
        origin="http",
        principal_id=kwargs.get("principal_id", "wa-u"),
        workspace_id=kwargs.get("workspace_id", "wa-ws"),
        idempotency_key=kwargs["idempotency_key"],
        input_payload={"capability_key": "integral_list_entries"},
    )
    claimed = await work_items.claim_due_candidate(
        worker_id="w1",
        work_item_id=item.work_item_id,
        lease_seconds=30,
    )
    assert claimed is not None
    return claimed


@pytest.mark.asyncio
async def test_propose_fail_closed_without_token() -> None:
    claimed = await _running_item(idempotency_key="wa-fc")
    with pytest.raises(WorkError) as exc:
        await work_approvals.propose_work_approval_unit(
            work_item_id=claimed.work_item_id,
            lease_token=claimed.lease_token,
            lease_fence=int(claimed.lease_fence or 0),
            run_id="run-1",
            run_step_id="capability:0",
            staging_token="",
            authority_digest="digest",
        )
    assert exc.value.code == "work.approval_required"


@pytest.mark.asyncio
async def test_propose_parks_waiting_for_human() -> None:
    claimed = await _running_item(idempotency_key="wa-park")
    approval, parked = await work_approvals.propose_work_approval_unit(
        work_item_id=claimed.work_item_id,
        lease_token=claimed.lease_token,
        lease_fence=int(claimed.lease_fence or 0),
        run_id="run-park",
        run_step_id="capability:0",
        staging_token="tok-park",
        staged_change_fields={
            "token": "tok-park",
            "user_id": "wa-u",
            "kind": "entry.create",
            "summary": "create entry",
            "state": "pending",
        },
        authority_digest="digest-park",
    )
    assert approval.status == "pending"
    assert approval.staging_token == "tok-park"
    assert parked.status == "waiting_for_human"
    assert parked.lease_token == ""


@pytest.mark.asyncio
async def test_approval_snapshots_the_work_definition() -> None:
    """A human decision remains auditable against the revision it reviewed."""
    claimed = await _running_item(idempotency_key="wa-definition")
    claimed.definition_id = "n.ApplicationDefinition.reviewed-revision"
    await claimed.save()

    approval, _ = await work_approvals.propose_work_approval_unit(
        work_item_id=claimed.work_item_id,
        lease_token=claimed.lease_token,
        lease_fence=int(claimed.lease_fence or 0),
        run_id="run-definition",
        run_step_id="capability:0",
        staging_token="tok-definition",
        authority_digest="digest-definition",
    )

    assert approval.definition_id == "n.ApplicationDefinition.reviewed-revision"


@pytest.mark.asyncio
async def test_approve_requeues_same_work_item() -> None:
    claimed = await _running_item(idempotency_key="wa-approve")
    approval, _ = await work_approvals.propose_work_approval_unit(
        work_item_id=claimed.work_item_id,
        lease_token=claimed.lease_token,
        lease_fence=int(claimed.lease_fence or 0),
        run_id="run-a",
        run_step_id="s1",
        staging_token="tok-a",
        authority_digest="d-a",
    )
    decided, item = await work_approvals.approve_work_approval(
        work_approval_id=approval.work_approval_id,
        decider_id="human-1",
    )
    assert decided.status == "approved"
    assert decided.decider_id == "human-1"
    assert item.work_item_id == claimed.work_item_id
    assert item.status == "queued"


@pytest.mark.asyncio
async def test_reject_terminalizes_policy_denied() -> None:
    claimed = await _running_item(idempotency_key="wa-reject")
    approval, _ = await work_approvals.propose_work_approval_unit(
        work_item_id=claimed.work_item_id,
        lease_token=claimed.lease_token,
        lease_fence=int(claimed.lease_fence or 0),
        run_id="run-r",
        run_step_id="s1",
        staging_token="tok-r",
        authority_digest="d-r",
    )
    decided, item = await work_approvals.reject_work_approval(
        work_approval_id=approval.work_approval_id,
        decider_id="human-2",
        reason="nope",
    )
    assert decided.status == "rejected"
    assert item.status == "failed"
    assert item.failure["code"] == "work.approval_rejected"
    assert item.failure["class"] == "policy_denied"


@pytest.mark.asyncio
async def test_one_decision_cas_blocks_second_approve() -> None:
    claimed = await _running_item(idempotency_key="wa-cas")
    approval, _ = await work_approvals.propose_work_approval_unit(
        work_item_id=claimed.work_item_id,
        lease_token=claimed.lease_token,
        lease_fence=int(claimed.lease_fence or 0),
        run_id="run-c",
        run_step_id="s1",
        staging_token="tok-c",
        authority_digest="d-c",
    )
    await work_approvals.approve_work_approval(
        work_approval_id=approval.work_approval_id,
        decider_id="h1",
    )
    with pytest.raises(WorkError) as exc:
        await work_approvals.reject_work_approval(
            work_approval_id=approval.work_approval_id,
            decider_id="h2",
        )
    assert exc.value.code == "work.approval_decided"
    loaded = await WorkApproval.get(
        work_approvals.work_approval_object_id(approval.work_approval_id)
    )
    assert loaded is not None
    assert loaded.status == "approved"


@pytest.mark.asyncio
async def test_expire_overdue_approval() -> None:
    claimed = await _running_item(idempotency_key="wa-exp")
    past = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    approval, _ = await work_approvals.propose_work_approval_unit(
        work_item_id=claimed.work_item_id,
        lease_token=claimed.lease_token,
        lease_fence=int(claimed.lease_fence or 0),
        run_id="run-e",
        run_step_id="s1",
        staging_token="tok-e",
        authority_digest="d-e",
        expires_at=past,
    )
    n = await work_approvals.expire_overdue_work_approvals()
    assert n >= 1
    loaded = await work_approvals.get_work_approval(approval.work_approval_id)
    assert loaded is not None
    assert loaded.status == "expired"
    item = await WorkItem.get(f"o.WorkItem.{claimed.work_item_id}")
    assert item is not None
    assert item.status == "expired"


@pytest.mark.asyncio
async def test_lookup_by_staging_token() -> None:
    claimed = await _running_item(idempotency_key="wa-lookup")
    approval, _ = await work_approvals.propose_work_approval_unit(
        work_item_id=claimed.work_item_id,
        lease_token=claimed.lease_token,
        lease_fence=int(claimed.lease_fence or 0),
        run_id="run-l",
        run_step_id="s1",
        staging_token="tok-lookup",
        authority_digest="d-l",
    )
    found = await work_approvals.get_pending_by_staging_token("tok-lookup")
    assert found is not None
    assert found.work_approval_id == approval.work_approval_id
