"""Linked WorkApproval decisions share the exact approved graph effect commit."""

import uuid

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.agentive.services import work_approvals, work_items
from app.services import approval_executor
from app.services.approved_effect import fact_fingerprint
from tests.contract.test_approved_effect_postgres import intent

pytestmark = [pytest.mark.asyncio, pytest.mark.contract, pytest.mark.postgres]


async def linked_intent(database):
    """Park actual work on the exact currently rooted policy approval."""
    owner, workspace, _, track, _, _, _, approval_id, _ = await intent()
    work = await work_items.enqueue_work_item(
        kind="capability",
        origin="http",
        principal_id=owner.id,
        workspace_id=workspace.id,
        idempotency_key=f"approved-link-{uuid.uuid4().hex}",
        input_payload={"capability_key": "integral_list_entries"},
    )
    claimed = await work_items.claim_due_candidate(
        worker_id="approval-qa", work_item_id=work.work_item_id, lease_seconds=30
    )
    assert claimed is not None
    linked, parked = await work_approvals.propose_work_approval_unit(
        work_item_id=claimed.work_item_id,
        lease_token=claimed.lease_token,
        lease_fence=claimed.lease_fence,
        run_id="approved-replay",
        run_step_id="capability:0",
        policy_approval_id=approval_id,
        authority_digest=fact_fingerprint(await database.get("node", approval_id)),
    )
    assert parked.status == "waiting_for_human"
    return owner, track, approval_id, linked, parked


@pytest.mark.parametrize("fault", [False, True])
async def test_linked_work_decision_and_graph_effect_are_atomic(
    postgres_raw_db, monkeypatch, fault
):
    """Success queues linked work; late audit fault rolls every stored effect back."""
    from app.services.app_operations import event_outbox

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        owner, track, approval_id, linked, work = await linked_intent(postgres_raw_db)
        work_id = f"o.WorkItem.{work.work_item_id}"
        link_id = work_approvals.work_approval_object_id(linked.work_approval_id)
        before_work = await postgres_raw_db.get("object", work_id)
        before_link = await postgres_raw_db.get("object", link_id)
        before_entries = await postgres_raw_db.find(
            "node", {"entity": "Entry", "context.track_id": track.id}, sort=[("id", 1)]
        )
        original = event_outbox.insert_revision_event

        async def late_fault(**kwargs):
            if kwargs["namespace"] == "approval-decision-v1":
                raise RuntimeError("after linked work decision")
            return await original(**kwargs)

        if fault:
            monkeypatch.setattr(event_outbox, "insert_revision_event", late_fault)
            with pytest.raises(RuntimeError, match="after linked work decision"):
                await approval_executor.execute_approval(
                    approval_id=approval_id, approver_user_id=owner.id
                )
            assert await postgres_raw_db.get("object", work_id) == before_work
            assert await postgres_raw_db.get("object", link_id) == before_link
            assert (
                await postgres_raw_db.find(
                    "node",
                    {"entity": "Entry", "context.track_id": track.id},
                    sort=[("id", 1)],
                )
                == before_entries
            )
            assert (await postgres_raw_db.get("node", approval_id))["context"][
                "status"
            ] == "pending"
        else:
            await approval_executor.execute_approval(
                approval_id=approval_id, approver_user_id=owner.id
            )
            assert (await postgres_raw_db.get("object", work_id))["context"][
                "status"
            ] == "queued"
            assert (await postgres_raw_db.get("object", link_id))["context"][
                "status"
            ] == "approved"
            assert (
                len(
                    await postgres_raw_db.find(
                        "node", {"entity": "Entry", "context.track_id": track.id}
                    )
                )
                == len(before_entries) + 1
            )
