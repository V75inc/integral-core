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
    item = await work_items.enqueue_work_item(
        kind="capability",
        origin="http",
        principal_id="pg-u-1",
        workspace_id="pg-ws-1",
        idempotency_key="pg-enq-1",
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
