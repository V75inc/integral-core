"""Postgres atomicity contracts for the durable work kernel (I-WORK-03)."""

from __future__ import annotations

import pytest
from jvspatial.db import get_prime_database

from app.agentive.services import work_items, work_outbox
from app.agentive.work_models import WorkItem, WorkOutboxEntry


def _postgres_db():
    return work_outbox._txn_database(get_prime_database())


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_postgres_enqueue_unit_commits_work_and_outbox() -> None:
    import uuid

    item = await work_items.enqueue_work_item(
        kind="capability",
        origin="http",
        principal_id="pg-u-1",
        workspace_id="pg-ws-1",
        idempotency_key=f"pg-enq-{uuid.uuid4().hex}",
        input_payload={"capability_key": "a"},
    )
    loaded = await WorkItem.get(item.id)
    assert loaded is not None
    assert loaded.status == "queued"
    outboxes = list(
        await WorkOutboxEntry.find({"context.work_item_id": item.work_item_id})
    )
    assert len(outboxes) == 1


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_postgres_persists_revision_bound_work_continuation() -> None:
    """A recovered worker can read plan state without chat-memory fallback."""
    import uuid

    item = await work_items.enqueue_work_item(
        kind="capability",
        origin="http",
        principal_id="pg-continuation-user",
        workspace_id="pg-continuation-workspace",
        idempotency_key=f"pg-continuation-{uuid.uuid4().hex}",
        input_payload={"capability_key": "app.apply"},
        plan_revision="plan:5",
        plan={"steps": ["apply", "verify"]},
        precommit_draft={"changes": [{"field": "status"}]},
        remaining_obligations=[{"key": "verify", "status": "pending"}],
    )
    loaded = await WorkItem.get(item.id)
    assert loaded is not None
    assert loaded.plan_revision == "plan:5"
    assert loaded.plan == {"steps": ["apply", "verify"]}
    assert loaded.precommit_draft == {"changes": [{"field": "status"}]}
    assert loaded.remaining_obligations == [{"key": "verify", "status": "pending"}]


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_postgres_enqueue_unit_rolls_back_both() -> None:
    """Injected failure after WorkItem insert rolls back outbox + work."""
    db = _postgres_db()
    object_id = work_items.work_item_object_id(
        kind="capability",
        origin="http",
        principal_id="pg-u-2",
        workspace_id="pg-ws-2",
        idempotency_key="pg-rb-1",
    )
    work_item_id = object_id.removeprefix("o.WorkItem.")
    outbox_id = work_outbox.outbox_id_for(
        work_item_id=work_item_id,
        topic=work_outbox.TOPIC_ENQUEUED,
        seq=0,
    )
    outbox_object = work_outbox.outbox_object_id(outbox_id)

    txn = await db.begin_transaction()
    work_doc = work_outbox.build_work_item_document(
        object_id=object_id,
        work_item_id=work_item_id,
        kind="capability",
        origin="http",
        principal_id="pg-u-2",
        workspace_id="pg-ws-2",
        idempotency_key="pg-rb-1",
        input_payload={"capability_key": "a"},
        input_fingerprint=work_items.input_fingerprint({"capability_key": "a"}),
        status="queued",
        attempt=0,
        transition_seq=0,
        next_attempt_at="2026-01-01T00:00:00+00:00",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        retry_policy={},
    )
    inserted = await txn.insert_if_absent("object", work_doc)
    assert inserted.created is True
    outbox_doc = work_outbox.build_outbox_document(
        outbox_id=outbox_id,
        work_item_id=work_item_id,
        topic=work_outbox.TOPIC_ENQUEUED,
        payload={"status": "queued"},
        created_at="2026-01-01T00:00:00+00:00",
    )
    await txn.insert_if_absent("object", outbox_doc)
    await db.rollback_transaction(txn)

    assert await db.get("object", object_id) is None
    assert await db.get("object", outbox_object) is None


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_postgres_transition_unit_rolls_back_status_and_outbox() -> None:
    item = await work_items.enqueue_work_item(
        kind="capability",
        origin="http",
        principal_id="pg-u-3",
        workspace_id="pg-ws-3",
        idempotency_key="pg-tr-rb",
        input_payload={"capability_key": "a"},
    )
    db = _postgres_db()
    next_seq = int(item.transition_seq or 0) + 1
    outbox_id = work_outbox.outbox_id_for(
        work_item_id=item.work_item_id,
        topic=work_outbox.TOPIC_TRANSITIONED,
        seq=next_seq,
    )
    txn = await db.begin_transaction()
    updated = await txn.find_one_and_update(
        "object",
        {"id": item.id, "context.status": "queued"},
        {
            "$set": {
                "context.status": "running",
                "context.transition_seq": next_seq,
                "context.attempt": 1,
            }
        },
    )
    assert updated is not None
    await txn.insert_if_absent(
        "object",
        work_outbox.build_outbox_document(
            outbox_id=outbox_id,
            work_item_id=item.work_item_id,
            topic=work_outbox.TOPIC_TRANSITIONED,
            payload={"from_status": "queued", "to_status": "running"},
            created_at="2026-01-01T00:00:00+00:00",
        ),
    )
    await db.rollback_transaction(txn)

    loaded = await WorkItem.get(item.id)
    assert loaded is not None
    assert loaded.status == "queued"
    assert int(loaded.transition_seq or 0) == 0
    assert await db.get("object", work_outbox.outbox_object_id(outbox_id)) is None


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_postgres_concurrent_claim_only_one_wins() -> None:
    import asyncio
    import uuid

    key = f"pg-claim-race-{uuid.uuid4().hex[:12]}"
    item = await work_items.enqueue_work_item(
        kind="capability",
        origin="http",
        principal_id="pg-u-4",
        workspace_id="pg-ws-4",
        idempotency_key=key,
        input_payload={"capability_key": "a"},
    )
    results = await asyncio.gather(
        work_items.claim_due_candidate(
            worker_id="pg-w1",
            lease_seconds=30,
            work_item_id=item.work_item_id,
        ),
        work_items.claim_due_candidate(
            worker_id="pg-w2",
            lease_seconds=30,
            work_item_id=item.work_item_id,
        ),
    )
    winners = [r for r in results if r is not None]
    assert len(winners) == 1
    loaded = await WorkItem.get(item.id)
    assert loaded is not None
    assert loaded.status == "running"
    assert loaded.attempt == 1
    assert loaded.lease_owner in {"pg-w1", "pg-w2"}


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_two_workers_exactly_one_claims() -> None:
    """Plan-named claim race gate — independent of the sibling test's key."""
    import asyncio
    import uuid

    key = f"pg-two-workers-{uuid.uuid4().hex[:12]}"
    item = await work_items.enqueue_work_item(
        kind="capability",
        origin="http",
        principal_id="pg-u-4b",
        workspace_id="pg-ws-4b",
        idempotency_key=key,
        input_payload={"capability_key": "a"},
    )
    results = await asyncio.gather(
        work_items.claim_due_candidate(
            worker_id="pg-w1",
            lease_seconds=30,
            work_item_id=item.work_item_id,
        ),
        work_items.claim_due_candidate(
            worker_id="pg-w2",
            lease_seconds=30,
            work_item_id=item.work_item_id,
        ),
    )
    winners = [r for r in results if r is not None]
    assert len(winners) == 1
    loaded = await WorkItem.get(item.id)
    assert loaded is not None
    assert loaded.status == "running"
    assert loaded.lease_owner in {"pg-w1", "pg-w2"}


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_stale_fence_cannot_complete_after_reclaim() -> None:
    import uuid

    from app.schemas.agentive.work import WorkError

    item = await work_items.enqueue_work_item(
        kind="capability",
        origin="http",
        principal_id="pg-u-5",
        workspace_id="pg-ws-5",
        idempotency_key=f"pg-stale-fence-{uuid.uuid4().hex[:12]}",
        input_payload={"capability_key": "a"},
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
    with pytest.raises(WorkError) as exc:
        await work_items.transition_leased(
            item.work_item_id,
            lease_token=stale_token,
            lease_fence=stale_fence,
            expected_status="running",
            target="succeeded",
        )
    assert exc.value.code == "work.lease_lost"


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_transition_and_outbox_are_atomic_on_rollback() -> None:
    """Plan-named rollback gate with its own idempotency key."""
    import uuid

    item = await work_items.enqueue_work_item(
        kind="capability",
        origin="http",
        principal_id="pg-u-3b",
        workspace_id="pg-ws-3b",
        idempotency_key=f"pg-tr-rb-alias-{uuid.uuid4().hex[:12]}",
        input_payload={"capability_key": "a"},
    )
    db = _postgres_db()
    next_seq = int(item.transition_seq or 0) + 1
    outbox_id = work_outbox.outbox_id_for(
        work_item_id=item.work_item_id,
        topic=work_outbox.TOPIC_TRANSITIONED,
        seq=next_seq,
    )
    txn = await db.begin_transaction()
    updated = await txn.find_one_and_update(
        "object",
        {"id": item.id, "context.status": "queued"},
        {
            "$set": {
                "context.status": "running",
                "context.transition_seq": next_seq,
                "context.attempt": 1,
            }
        },
    )
    assert updated is not None
    await txn.insert_if_absent(
        "object",
        work_outbox.build_outbox_document(
            outbox_id=outbox_id,
            work_item_id=item.work_item_id,
            topic=work_outbox.TOPIC_TRANSITIONED,
            payload={"from_status": "queued", "to_status": "running"},
            created_at="2026-01-01T00:00:00+00:00",
        ),
    )
    await db.rollback_transaction(txn)

    loaded = await WorkItem.get(item.id)
    assert loaded is not None
    assert loaded.status == "queued"
    assert int(loaded.transition_seq or 0) == 0
    assert await db.get("object", work_outbox.outbox_object_id(outbox_id)) is None


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_two_approval_decisions_exactly_one_wins() -> None:
    import asyncio
    import uuid

    from app.agentive.services import work_approvals
    from app.schemas.agentive.work import WorkError

    item = await work_items.enqueue_work_item(
        kind="capability",
        origin="http",
        principal_id="pg-u-6",
        workspace_id="pg-ws-6",
        idempotency_key=f"pg-appr-race-{uuid.uuid4().hex[:12]}",
        input_payload={"capability_key": "a"},
    )
    claimed = await work_items.claim_due_candidate(
        worker_id="w", work_item_id=item.work_item_id, lease_seconds=30
    )
    assert claimed is not None
    approval, _ = await work_approvals.propose_work_approval_unit(
        work_item_id=claimed.work_item_id,
        lease_token=claimed.lease_token,
        lease_fence=int(claimed.lease_fence or 0),
        run_id="run",
        run_step_id="s",
        staging_token="pg-tok",
        authority_digest="digest",
    )

    async def _approve():
        try:
            return await work_approvals.approve_work_approval(
                work_approval_id=approval.work_approval_id,
                decider_id="h1",
            )
        except WorkError as exc:
            return exc

    async def _reject():
        try:
            return await work_approvals.reject_work_approval(
                work_approval_id=approval.work_approval_id,
                decider_id="h2",
            )
        except WorkError as exc:
            return exc

    results = await asyncio.gather(_approve(), _reject())
    wins = [r for r in results if not isinstance(r, WorkError)]
    loses = [r for r in results if isinstance(r, WorkError)]
    assert len(wins) == 1
    assert len(loses) == 1
    assert loses[0].code == "work.approval_decided"


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_recovery_converges_without_cross_workspace_claim() -> None:
    import uuid

    from app.agentive.services import work_recovery

    suffix = uuid.uuid4().hex[:12]
    a = await work_items.enqueue_work_item(
        kind="capability",
        origin="http",
        principal_id="pg-u-7a",
        workspace_id="pg-ws-7a",
        idempotency_key=f"pg-rec-a-{suffix}",
        input_payload={"capability_key": "a"},
    )
    b = await work_items.enqueue_work_item(
        kind="capability",
        origin="http",
        principal_id="pg-u-7b",
        workspace_id="pg-ws-7b",
        idempotency_key=f"pg-rec-b-{suffix}",
        input_payload={"capability_key": "a"},
    )
    claimed_a = await work_items.claim_due_candidate(
        worker_id="wa", work_item_id=a.work_item_id, lease_seconds=30
    )
    assert claimed_a is not None
    await work_items.force_expire_lease_for_tests(a.work_item_id)
    report = await work_recovery.run_recovery_pass(reclaim_worker_id="pg-recovery")
    assert report.reclaimed >= 1
    loaded_a = await WorkItem.get(a.id)
    loaded_b = await WorkItem.get(b.id)
    assert loaded_a is not None and loaded_b is not None
    assert loaded_a.workspace_id == "pg-ws-7a"
    assert loaded_b.workspace_id == "pg-ws-7b"
    assert loaded_b.status == "queued"
    if loaded_a.status == "running":
        assert loaded_a.lease_owner == "pg-recovery"
