"""Independent delivery cannot extend a save or lose/reorder its audit facts."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.services.app_operations import event_outbox as outbox
from app.services.app_operations.transaction_scope import postgres_graph_transaction
from app.services.change_event_logger import get_change_event_logger

pytestmark = [pytest.mark.postgres, pytest.mark.contract, pytest.mark.asyncio]


async def enqueue(db, key="probe", *, fail=False):
    """Commit a synthetic event without any user data."""
    async with postgres_graph_transaction(db) as tx:
        oid = await outbox.insert_revision_event(
            transaction=tx,
            workspace_id="probe",
            namespace=key,
            subject_id="n.Entry.probe",
            revision=1,
            event={
                "actor_kind": "system",
                "actor_id": "system",
                "action": "entry.create",
                "resource_type": "Entry",
                "resource_id": "n.Entry.probe",
                "before": None,
                "after": {"title": "synthetic"},
                "scope": "track:n.Track.probe",
            },
        )
        if fail:
            raise RuntimeError("rollback")
    return oid


async def row(db, oid):
    """Read a fresh outbox fact from primary storage."""
    return await db.get("object", outbox.event_outbox_object_id(oid))


async def update(db, oid, **fields):
    """Change synthetic recovery state through a held transaction."""
    async with postgres_graph_transaction(db) as tx:
        await tx.find_one_and_update(
            "object",
            {"id": outbox.event_outbox_object_id(oid)},
            {"$set": {"context." + key: value for key, value in fields.items()}},
        )


async def test_enqueue_rolls_back_and_request_never_delivers(
    postgres_raw_db, monkeypatch
):
    """Rollback removes intent; successful enqueue never invokes delivery."""

    async def forbidden(**kwargs):
        raise AssertionError("save attempted audit delivery")

    monkeypatch.setattr(outbox, "deliver_operation_event", forbidden)
    with pytest.raises(RuntimeError, match="rollback"):
        await enqueue(postgres_raw_db, fail=True)
    assert not await postgres_raw_db.find("object", {"entity": "OperationEventOutbox"})
    oid = await enqueue(postgres_raw_db)
    await outbox.request_event_delivery(outbox_id=oid)
    assert (await row(postgres_raw_db, oid))["context"]["status"] == "pending"


async def test_audit_is_idempotent_across_crash_before_ack(postgres_raw_db):
    """A lost acknowledgement cannot create a second audit row."""
    oid = await enqueue(postgres_raw_db)
    assert await outbox.deliver_operation_event(outbox_id=oid, database=postgres_raw_db)
    log = get_change_event_logger()
    first = await log.find_after_checkpoint(limit=100)
    assert len(first) == 1
    assert first[0].log_data["workspace_id"] == "probe"
    await update(postgres_raw_db, oid, status="pending", attempt=0)
    assert await outbox.deliver_operation_event(outbox_id=oid, database=postgres_raw_db)
    second = await log.find_after_checkpoint(limit=100)
    assert [item.id for item in second] == [item.id for item in first]


async def test_late_event_keeps_occurrence_time_and_advances_feed(postgres_raw_db):
    """Recovery retains occurrence time and follows the delivery watermark."""
    old = await enqueue(postgres_raw_db, "late")
    occurred = "2020-01-01T00:00:00+00:00"
    await update(postgres_raw_db, old, created_at=occurred)
    recent = await enqueue(postgres_raw_db, "recent")
    await outbox.deliver_operation_event(outbox_id=recent, database=postgres_raw_db)
    log = get_change_event_logger()
    first = (await log.find_after_checkpoint(limit=100))[0]
    await outbox.deliver_operation_event(outbox_id=old, database=postgres_raw_db)
    next_rows = await log.find_after_checkpoint(
        last_logged_at=first.logged_at.isoformat(), last_event_id=first.id
    )
    assert len(next_rows) == 1
    from app.services.change_event_logger import envelope_from_dblog

    event = envelope_from_dblog(next_rows[0])
    assert event.ts == occurred
    assert event.delivery_ts > first.logged_at.isoformat()


async def test_logging_outage_retries_then_dead_letters(postgres_raw_db, monkeypatch):
    """Log outages retain facts, back off, and eventually require replay."""
    oid = await enqueue(postgres_raw_db)

    async def unavailable(*args: object, **kwargs: object):
        raise RuntimeError("synthetic logging outage")

    monkeypatch.setattr(get_change_event_logger(), "persist", unavailable)
    for attempt in range(5):
        await update(postgres_raw_db, oid, next_attempt_at="")
        with pytest.raises(RuntimeError, match="synthetic"):
            await outbox.deliver_operation_event(
                outbox_id=oid, database=postgres_raw_db
            )
        saved = (await row(postgres_raw_db, oid))["context"]
        assert saved["attempt"] == attempt + 1
        assert saved["next_attempt_at"] > saved["updated_at"]
    assert saved["status"] == "dead_letter"


async def test_competing_claim_and_stale_owner_cannot_ack(postgres_raw_db, monkeypatch):
    """Competing workers cannot claim live leases or acknowledge as stale owners."""
    oid = await enqueue(postgres_raw_db)
    entered, release = asyncio.Event(), asyncio.Event()
    original = get_change_event_logger().persist

    async def paused(event):
        entered.set()
        await release.wait()
        return await original(event)

    monkeypatch.setattr(get_change_event_logger(), "persist", paused)
    first = asyncio.create_task(
        outbox.deliver_operation_event(outbox_id=oid, database=postgres_raw_db)
    )
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert not await outbox.deliver_operation_event(
            outbox_id=oid, database=postgres_raw_db
        )
        await update(postgres_raw_db, oid, lease_owner="replacement")
        release.set()
        assert not await first
        assert (await row(postgres_raw_db, oid))["context"]["status"] == "delivering"
    finally:
        release.set()
        await asyncio.gather(first, return_exceptions=True)


async def test_recovery_skips_not_due_rows_before_limit(postgres_raw_db, monkeypatch):
    """Deferred failures cannot starve a bounded page of ready events."""
    monkeypatch.setattr(outbox, "_BATCH_SIZE", 1)
    blocked = await enqueue(postgres_raw_db, "not-due")
    await update(
        postgres_raw_db,
        blocked,
        next_attempt_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
    )
    ready = await enqueue(postgres_raw_db, "ready")
    assert await outbox.deliver_pending_operation_events(database=postgres_raw_db) == 1
    assert (await row(postgres_raw_db, ready))["context"]["status"] == "delivered"
    assert (await row(postgres_raw_db, blocked))["context"]["status"] == "pending"


async def test_consumers_have_independent_transactional_checkpoints(postgres_raw_db):
    """A failed consumer does not repeat successful consumers or audit writes."""
    from app.services.event_subscription_registry import (
        register_consumer_hook,
        reset_consumer_hooks,
    )
    from app.services.event_worker import consume_registered_hooks

    oid = await enqueue(postgres_raw_db)
    await outbox.deliver_operation_event(outbox_id=oid, database=postgres_raw_db)
    seen, attempts = [], []

    async def good(event):
        seen.append(event.id)

    async def failing(event):
        attempts.append(event.id)
        raise RuntimeError("synthetic consumer failure")

    reset_consumer_hooks()
    register_consumer_hook(good)
    register_consumer_hook(failing)
    try:
        await consume_registered_hooks()
        await consume_registered_hooks()
        assert len(seen) == 1
        assert len(attempts) == 1  # Capped backoff prevents a hot retry loop.
        assert (
            len(await get_change_event_logger().find_after_checkpoint(limit=100)) == 1
        )
    finally:
        reset_consumer_hooks()


async def test_later_consumer_failure_keeps_completed_checkpoint(postgres_raw_db):
    """A poison second event cannot repeat a completed first callback."""
    from app.services.event_worker import _consume_hook

    for key in ("first", "second"):
        oid = await enqueue(postgres_raw_db, key)
        await outbox.deliver_operation_event(outbox_id=oid, database=postgres_raw_db)
    rows = await get_change_event_logger().find_after_checkpoint(limit=100)
    seen = []

    async def partial(event):
        if event.id == rows[1].id:
            raise RuntimeError("synthetic second event failure")
        seen.append(event.id)

    await _consume_hook(partial)
    await _consume_hook(partial)
    assert seen == [rows[0].id]


async def test_consumer_dead_letter_and_explicit_replay(postgres_raw_db):
    """Consumer failure is bounded and replay preserves its checkpoint."""
    from app.services.event_worker import _consume_hook, replay, status

    oid = await enqueue(postgres_raw_db)
    await outbox.deliver_operation_event(outbox_id=oid, database=postgres_raw_db)
    attempts = []

    async def poison(event):
        attempts.append(event.id)
        raise RuntimeError("synthetic poison event")

    for _ in range(5):
        await _consume_hook(poison)
        for checkpoint in await postgres_raw_db.find(
            "object", {"entity": "ChangeEventTriggerCheckpoint"}
        ):
            async with postgres_graph_transaction(postgres_raw_db) as tx:
                await tx.find_one_and_update(
                    "object",
                    {"id": checkpoint["id"]},
                    {"$set": {"context.delivery_next_attempt_at": ""}},
                )
    await _consume_hook(poison)
    assert len(attempts) == 5
    health = await status(postgres_raw_db)
    assert health["consumer_dead_letters"] == 1
    assert health["dead_letters"] == 1
    assert await replay(health["consumer_replay_ids"][0])
    await _consume_hook(poison)
    assert len(attempts) == 6


async def test_cli_worker_is_an_independent_process(postgres_raw_db):
    """The actual CLI drains synthetic events without booting the API module."""
    import os
    import sys

    oid = await enqueue(postgres_raw_db)
    env = dict(os.environ)
    env["JVSPATIAL_LOG_POSTGRES_DSN"] = env["JVSPATIAL_POSTGRES_DSN"]
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import sys; from app.cli import main; "
        "result=main(['worker','events','--once']); "
        "assert result == 0; assert 'app.main' not in sys.modules",
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=120)
    assert process.returncode == 0, stderr.decode()[-3000:]
    assert (await row(postgres_raw_db, oid))["context"]["status"] == "delivered"


async def test_long_delivery_renews_its_lease(postgres_raw_db, monkeypatch):
    """A delivery that spans renewal intervals retains its claim until completion."""
    monkeypatch.setattr(outbox, "_LEASE_SECONDS", 0.15)
    oid = await enqueue(postgres_raw_db)
    entered, release = asyncio.Event(), asyncio.Event()
    original = get_change_event_logger().persist

    async def paused(event):
        entered.set()
        await release.wait()
        return await original(event)

    monkeypatch.setattr(get_change_event_logger(), "persist", paused)
    task = asyncio.create_task(
        outbox.deliver_operation_event(outbox_id=oid, database=postgres_raw_db)
    )
    try:
        await asyncio.wait_for(entered.wait(), 5)
        initial = (await row(postgres_raw_db, oid))["context"]["lease_until"]

        async def renewed():
            while (await row(postgres_raw_db, oid))["context"][
                "lease_until"
            ] == initial:
                await asyncio.sleep(0.03)

        await asyncio.wait_for(renewed(), 3)
        release.set()
        assert await task
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_missing_log_store_never_acknowledges_delivery(
    postgres_raw_db, monkeypatch
):
    """Unregistered logging storage keeps the durable fact eligible for recovery."""
    oid = await enqueue(postgres_raw_db)
    monkeypatch.setattr(get_change_event_logger(), "_get_log_context", lambda: None)
    with pytest.raises(RuntimeError, match="logging database unavailable"):
        await outbox.deliver_operation_event(outbox_id=oid, database=postgres_raw_db)
    assert (await row(postgres_raw_db, oid))["context"]["status"] == "pending"


async def test_stalled_consumer_does_not_block_audit_delivery(postgres_raw_db):
    """A separate process keeps delivering audits while a callback is suspended."""
    import os
    import sys

    first = await enqueue(postgres_raw_db, "before-stall")
    env = dict(os.environ)
    env["JVSPATIAL_LOG_POSTGRES_DSN"] = env["JVSPATIAL_POSTGRES_DSN"]
    script = """
import asyncio
from app.services.event_subscription_registry import register_consumer_hook
from app.services.event_worker import main
async def stalled(event):
    print("CONSUMER_BLOCKED", flush=True)
    await asyncio.Event().wait()
register_consumer_hook(stalled)
asyncio.run(main())
"""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:

        async def blocked():
            while True:
                line = await process.stdout.readline()
                if b"CONSUMER_BLOCKED" in line:
                    return
                if not line:
                    raise AssertionError("consumer process exited before delivery")

        await asyncio.wait_for(blocked(), 120)
        assert (await row(postgres_raw_db, first))["context"]["status"] == "delivered"
        second = await enqueue(postgres_raw_db, "during-stall")

        async def delivered():
            while (await row(postgres_raw_db, second))["context"][
                "status"
            ] != "delivered":
                await asyncio.sleep(0.05)

        await asyncio.wait_for(delivered(), 10)
    finally:
        if process.returncode is None:
            process.terminate()
        try:
            await asyncio.wait_for(process.communicate(), 10)
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
