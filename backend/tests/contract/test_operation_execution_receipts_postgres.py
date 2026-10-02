"""Live Postgres contracts for transactional command receipt execution."""

from __future__ import annotations

import asyncio

import pytest
from jvspatial.core import Node

from app.contracts.operations import OperationIdentity, canonical_request_hash
from app.services.app_operations.event_outbox import (
    deliver_operation_event,
    event_outbox_id,
    event_outbox_object_id,
)
from app.services.app_operations.execution_receipts import (
    OperationReceiptConflict,
    execute_operation_once,
    receipt_object_id,
    receipt_reference,
)


class ReceiptProbeNode(Node):
    """Small graph type used only to prove receipt/effect atomicity."""

    label: str = ""


def _identity(key: str = "receipt-key") -> OperationIdentity:
    return OperationIdentity.create(
        workspace_id="ws-receipt",
        app_id="n.App.receipt",
        operation_key="record.create",
        principal_id="u-receipt",
        idempotency_key=key,
    )


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_receipt_commits_with_graph_effect_and_replays(postgres_raw_db) -> None:
    identity = _identity()
    request_hash = canonical_request_hash({"label": "first"})
    executed = 0

    async def create_effect():
        nonlocal executed
        executed += 1
        node = await ReceiptProbeNode.create(label="first")
        return {"node_id": node.id, "label": node.label}

    first = await execute_operation_once(
        identity=identity,
        request_hash=request_hash,
        execute=create_effect,
        database=postgres_raw_db,
    )
    replay = await execute_operation_once(
        identity=identity,
        request_hash=request_hash,
        execute=create_effect,
        database=postgres_raw_db,
    )

    assert executed == 1
    assert first.replayed is False
    assert replay.replayed is True
    assert replay.result == first.result
    assert await postgres_raw_db.get("node", first.result["node_id"]) is not None
    receipt = await postgres_raw_db.get("object", receipt_object_id(identity))
    assert receipt is not None
    assert receipt["context"]["status"] == "succeeded"
    assert receipt_reference(identity, replayed=False) == {
        "id": receipt_object_id(identity),
        "status": "succeeded",
        "replayed": False,
    }


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_receipt_and_graph_effect_roll_back_when_handler_fails(
    postgres_raw_db,
) -> None:
    identity = _identity("rollback-key")
    request_hash = canonical_request_hash({"label": "rollback"})
    node_id = ""

    async def fail_after_write():
        nonlocal node_id
        node = await ReceiptProbeNode.create(label="rollback")
        node_id = node.id
        raise RuntimeError("injected handler failure")

    with pytest.raises(RuntimeError, match="injected handler failure"):
        await execute_operation_once(
            identity=identity,
            request_hash=request_hash,
            execute=fail_after_write,
            database=postgres_raw_db,
        )

    assert await postgres_raw_db.get("node", node_id) is None
    assert await postgres_raw_db.get("object", receipt_object_id(identity)) is None


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_concurrent_retries_run_one_handler_and_replay_one_receipt(
    postgres_raw_db,
) -> None:
    identity = _identity("race-key")
    request_hash = canonical_request_hash({"label": "race"})
    winner_started = asyncio.Event()
    calls = 0

    async def winner():
        nonlocal calls
        calls += 1
        winner_started.set()
        await asyncio.sleep(0.1)
        node = await ReceiptProbeNode.create(label="race")
        return {"node_id": node.id}

    async def loser():
        nonlocal calls
        calls += 1
        raise AssertionError("concurrent loser must not execute")

    first_task = asyncio.create_task(
        execute_operation_once(
            identity=identity,
            request_hash=request_hash,
            execute=winner,
            database=postgres_raw_db,
        )
    )
    await asyncio.wait_for(winner_started.wait(), timeout=1)
    second_task = asyncio.create_task(
        execute_operation_once(
            identity=identity,
            request_hash=request_hash,
            execute=loser,
            database=postgres_raw_db,
        )
    )
    first, second = await asyncio.gather(first_task, second_task)

    assert calls == 1
    assert sorted((first.replayed, second.replayed)) == [False, True]
    assert first.result == second.result


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_reused_key_with_changed_request_fails_before_handler(
    postgres_raw_db,
) -> None:
    identity = _identity("conflict-key")
    first_hash = canonical_request_hash({"label": "first"})
    calls = 0

    async def create_effect():
        nonlocal calls
        calls += 1
        return {"ok": True}

    await execute_operation_once(
        identity=identity,
        request_hash=first_hash,
        execute=create_effect,
        database=postgres_raw_db,
    )
    with pytest.raises(OperationReceiptConflict):
        await execute_operation_once(
            identity=identity,
            request_hash=canonical_request_hash({"label": "different"}),
            execute=create_effect,
            database=postgres_raw_db,
        )
    assert calls == 1


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_receipt_commits_deferred_event_with_graph_effect(
    postgres_raw_db,
) -> None:
    """The event fact is durable only when the command itself commits."""
    identity = _identity("event-outbox-key")
    request_hash = canonical_request_hash({"label": "event"})
    event = {
        "actor_kind": "human",
        "actor_id": "u-receipt",
        "action": "entry.create",
        "resource_type": "Entry",
        "resource_id": "n.Entry.event",
        "before": None,
        "after": {"title": "event"},
        "scope": "track:n.Track.event",
    }

    async def create_effect():
        node = await ReceiptProbeNode.create(label="event")
        return {"node_id": node.id}

    result = await execute_operation_once(
        identity=identity,
        request_hash=request_hash,
        execute=create_effect,
        event_outbox=[event],
        database=postgres_raw_db,
    )

    assert await postgres_raw_db.get("node", result.result["node_id"]) is not None
    outbox = await postgres_raw_db.get(
        "object", event_outbox_object_id(event_outbox_id(identity, 0))
    )
    assert outbox is not None
    assert outbox["context"]["status"] == "pending"
    assert outbox["context"]["event"] == event


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_a06_crash_then_retry_commits_one_effect_and_one_receipt(
    postgres_raw_db,
) -> None:
    """A crash before commit leaves nothing; the retry commits one node and one receipt.

    A two-connection race deadlocks while the crashing transaction still
    holds the receipt claim, so this proof stays sequential. A06 stays
    failed until a concurrent command is in the same trace.
    """
    identity = _identity("c6-crash-key")
    request_hash = canonical_request_hash({"label": "c6-crash"})

    async def crash_after_write():
        await ReceiptProbeNode.create(label="c6-crash")
        raise RuntimeError("injected crash before receipt completion")

    with pytest.raises(RuntimeError, match="injected crash"):
        await execute_operation_once(
            identity=identity,
            request_hash=request_hash,
            execute=crash_after_write,
            database=postgres_raw_db,
        )

    assert await postgres_raw_db.get("object", receipt_object_id(identity)) is None

    async def commit_once():
        node = await ReceiptProbeNode.create(label="c6-crash")
        return {"node_id": node.id}

    first = await execute_operation_once(
        identity=identity,
        request_hash=request_hash,
        execute=commit_once,
        database=postgres_raw_db,
    )
    second = await execute_operation_once(
        identity=identity,
        request_hash=request_hash,
        execute=commit_once,
        database=postgres_raw_db,
    )
    assert first.replayed is False
    assert second.replayed is True
    assert first.result == second.result
    nodes = await postgres_raw_db.find("node", {"context.label": "c6-crash"})
    assert len(nodes) == 1


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_a11_unknown_outbox_reconciles_before_retry(postgres_raw_db) -> None:
    """A persisted unknown outbox row is not redelivered until it is observed."""
    from app.services.app_operations.transaction_scope import postgres_graph_transaction

    identity = _identity("c6-unknown")
    request_hash = canonical_request_hash({"label": "c6-unknown"})
    event = {
        "action": "entry.create",
        "resource_type": "Entry",
        "resource_id": "n.Entry.unknown",
        "before": None,
        "after": {"title": "unknown"},
        "scope": "track:n.Track.unknown",
    }

    async def create_effect():
        node = await ReceiptProbeNode.create(label="c6-unknown")
        return {"node_id": node.id}

    await execute_operation_once(
        identity=identity,
        request_hash=request_hash,
        execute=create_effect,
        event_outbox=[event],
        database=postgres_raw_db,
    )
    outbox_id = event_outbox_id(identity, 0)
    object_id = event_outbox_object_id(outbox_id)
    async with postgres_graph_transaction(postgres_raw_db) as transaction:
        record = await transaction.get("object", object_id)
        record["context"]["status"] = "unknown"
        await transaction.save("object", record)

    assert (
        await deliver_operation_event(outbox_id=outbox_id, database=postgres_raw_db)
        is False
    )
    waiting = await postgres_raw_db.get("object", object_id)
    assert waiting["context"]["status"] == "unknown"

    async with postgres_graph_transaction(postgres_raw_db) as transaction:
        record = await transaction.get("object", object_id)
        record["context"]["provider_result"] = {"ok": True}
        await transaction.save("object", record)

    assert (
        await deliver_operation_event(outbox_id=outbox_id, database=postgres_raw_db)
        is True
    )
    assert (
        await deliver_operation_event(outbox_id=outbox_id, database=postgres_raw_db)
        is False
    )
    delivered = await postgres_raw_db.get("object", object_id)
    assert delivered["context"]["status"] == "delivered"
