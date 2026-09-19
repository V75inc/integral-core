"""WorkItem + WorkOutboxEntry atomic units and delivery (Task 2)."""

from __future__ import annotations

import pytest

from app.agentive.services import work_items
from app.agentive.work_models import WorkItem, WorkOutboxEntry


@pytest.mark.asyncio
async def test_enqueue_creates_work_item_and_initial_outbox() -> None:
    item = await work_items.enqueue_work_item(
        kind="capability",
        origin="http",
        principal_id="u-out-1",
        workspace_id="ws-out-1",
        idempotency_key="enq-1",
        input_payload={"capability_key": "integral_list_entries"},
    )
    outboxes = list(
        await WorkOutboxEntry.find({"context.work_item_id": item.work_item_id})
    )
    assert len(outboxes) == 1
    entry = outboxes[0]
    assert entry.topic == "work.enqueued"
    assert entry.status == "pending"
    assert entry.outbox_id
    assert entry.id == f"o.WorkOutboxEntry.{entry.outbox_id}"


@pytest.mark.asyncio
async def test_transition_emits_outbox_fact() -> None:
    item = await work_items.enqueue_work_item(
        kind="capability",
        origin="http",
        principal_id="u-out-2",
        workspace_id="ws-out-2",
        idempotency_key="tr-1",
        input_payload={"capability_key": "a"},
    )
    running = await work_items.transition_work_item(
        item.work_item_id,
        expected_status="queued",
        target="running",
        fields={"attempt": 1},
    )
    assert running.status == "running"
    facts = list(
        await WorkOutboxEntry.find(
            {
                "context.work_item_id": item.work_item_id,
                "context.topic": "work.transitioned",
            }
        )
    )
    assert len(facts) == 1
    assert facts[0].payload.get("to_status") == "running"
    assert facts[0].payload.get("from_status") == "queued"


@pytest.mark.asyncio
async def test_duplicate_delivery_dedupes_by_outbox_id() -> None:
    from app.agentive.services import work_outbox

    item = await work_items.enqueue_work_item(
        kind="capability",
        origin="http",
        principal_id="u-out-3",
        workspace_id="ws-out-3",
        idempotency_key="dedupe-1",
        input_payload={"capability_key": "a"},
    )
    entries = list(
        await WorkOutboxEntry.find({"context.work_item_id": item.work_item_id})
    )
    assert len(entries) == 1
    entry = entries[0]
    seen: list[str] = []

    async def consumer(e: WorkOutboxEntry) -> None:
        seen.append(e.outbox_id)

    first = await work_outbox.deliver_outbox_entry(entry, consumer)
    second = await work_outbox.deliver_outbox_entry(entry, consumer)
    assert first is True
    assert second is False
    assert seen == [entry.outbox_id]
    reloaded = await WorkOutboxEntry.get(entry.id)
    assert reloaded is not None
    assert reloaded.status == "delivered"


@pytest.mark.asyncio
async def test_failed_consumer_retries_without_losing_entry() -> None:
    from app.agentive.services import work_outbox

    item = await work_items.enqueue_work_item(
        kind="capability",
        origin="http",
        principal_id="u-out-4",
        workspace_id="ws-out-4",
        idempotency_key="retry-1",
        input_payload={"capability_key": "a"},
    )
    entry = (await WorkOutboxEntry.find({"context.work_item_id": item.work_item_id}))[0]
    calls = {"n": 0}

    async def boom(_e: WorkOutboxEntry) -> None:
        calls["n"] += 1
        raise RuntimeError("downstream down")

    ok = await work_outbox.deliver_outbox_entry(entry, boom)
    assert ok is False
    assert calls["n"] == 1
    reloaded = await WorkOutboxEntry.get(entry.id)
    assert reloaded is not None
    assert reloaded.status == "pending"
    assert reloaded.attempt == 1
    assert reloaded.available_at  # deferred

    async def ok_consumer(_e: WorkOutboxEntry) -> None:
        return None

    # Force available now for retry.
    reloaded.available_at = ""
    await reloaded.save()
    assert await work_outbox.deliver_outbox_entry(reloaded, ok_consumer) is True
    final = await WorkOutboxEntry.get(entry.id)
    assert final is not None
    assert final.status == "delivered"


@pytest.mark.asyncio
async def test_development_reconciliation_backfills_missing_outbox() -> None:
    from app.agentive.services import work_outbox

    # Simulate crash after WorkItem create before outbox: create WorkItem alone.
    object_id = work_items.work_item_object_id(
        kind="capability",
        origin="http",
        principal_id="u-out-5",
        workspace_id="ws-out-5",
        idempotency_key="recon-1",
    )
    work_item_id = object_id.removeprefix("o.WorkItem.")
    fingerprint = work_items.input_fingerprint({"capability_key": "a"})
    item, created = await WorkItem.create_if_absent(
        id=object_id,
        work_item_id=work_item_id,
        kind="capability",
        origin="http",
        principal_id="u-out-5",
        workspace_id="ws-out-5",
        idempotency_key="recon-1",
        input_payload={"capability_key": "a"},
        input_fingerprint=fingerprint,
        status="queued",
        attempt=0,
        transition_seq=0,
        next_attempt_at="2026-01-01T00:00:00+00:00",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )
    assert created is True
    before = list(
        await WorkOutboxEntry.find({"context.work_item_id": item.work_item_id})
    )
    assert before == []
    n = await work_outbox.reconcile_missing_outbox_facts(item)
    assert n == 1
    after = list(
        await WorkOutboxEntry.find({"context.work_item_id": item.work_item_id})
    )
    assert len(after) == 1
    assert after[0].topic == "work.enqueued"
    # Idempotent.
    assert await work_outbox.reconcile_missing_outbox_facts(item) == 0


@pytest.mark.asyncio
async def test_enqueue_idempotent_reuse_does_not_duplicate_outbox() -> None:
    first = await work_items.enqueue_work_item(
        kind="capability",
        origin="http",
        principal_id="u-out-6",
        workspace_id="ws-out-6",
        idempotency_key="same-out",
        input_payload={"capability_key": "a"},
    )
    second = await work_items.enqueue_work_item(
        kind="capability",
        origin="http",
        principal_id="u-out-6",
        workspace_id="ws-out-6",
        idempotency_key="same-out",
        input_payload={"capability_key": "a"},
    )
    assert first.id == second.id
    outboxes = list(
        await WorkOutboxEntry.find({"context.work_item_id": first.work_item_id})
    )
    assert len(outboxes) == 1
