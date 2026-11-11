"""Independent durable audit delivery process; no HTTP or resident bootstrap."""

from __future__ import annotations

import asyncio
import logging
import signal
from datetime import datetime, timedelta, timezone

from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)


async def initialise(*, ensure_indexes=True) -> None:
    """Initialize only the stores and consumers needed by event delivery."""
    from jvspatial.logging import initialize_logging_database
    from jvspatial.logging.config import get_logging_config

    from app.services.change_event_logger import get_change_event_logger
    from app.services.db_init import init_prime_db

    if get_logging_config().get("db_type") not in {"postgres", "postgresql"}:
        raise RuntimeError("The event worker requires a PostgreSQL logging database")
    from app.services.performance import initialize_tracing

    initialize_tracing()
    init_prime_db()
    if not initialize_logging_database(log_levels=set(), enable_api_endpoints=False):
        raise RuntimeError("Event worker logging database unavailable")
    if ensure_indexes:
        await get_change_event_logger().ensure_indexes()
    from app.agentive.sample_consumers import register_sample_consumers

    register_sample_consumers()
    from jvspatial.db import get_prime_database

    db = get_prime_database()
    from app.services.app_operations.transaction_scope import postgres_graph_transaction
    from app.services.entry_write_scope import is_postgres_entry_transaction

    async with postgres_graph_transaction(db) as held:
        if not is_postgres_entry_transaction(held):
            raise RuntimeError("The event worker requires a PostgreSQL primary store")
    if ensure_indexes:
        from jvspatial.core.context import get_default_context

        from app.agentive import work_models

        for model in (
            work_models.WorkItem,
            work_models.WorkOutboxEntry,
            work_models.EventTriggerDeclaration,
            work_models.ChangeEventTriggerCheckpoint,
        ):
            await get_default_context().ensure_indexes(model)
        await db.create_index(
            "object", [("context.status", 1), ("context.updated_at", 1), ("id", 1)]
        )


async def status(database=None) -> dict:
    """Return content-free queue health and the last worker heartbeat."""
    from jvspatial.db import get_prime_database

    db = database or get_prime_database()
    base = {"entity": "OperationEventOutbox"}
    pending = {**base, "context.status": {"$in": ["pending", "delivering"]}}
    oldest = await db.find("object", pending, limit=1, sort=[("context.created_at", 1)])
    age = 0.0
    if oldest:
        created = oldest[0]["context"]["created_at"]
        age = max(
            0,
            (
                datetime.now(timezone.utc) - datetime.fromisoformat(created)
            ).total_seconds(),
        )
    heartbeat = await db.get("object", "o.EventWorkerHeartbeat.events")
    heartbeat_age = None
    if heartbeat:
        heartbeat_age = max(
            0,
            (
                datetime.now(timezone.utc)
                - datetime.fromisoformat(heartbeat["context"]["updated_at"])
            ).total_seconds(),
        )
    consumer_filter = {
        "entity": "ChangeEventTriggerCheckpoint",
        "context.delivery_status": "dead_letter",
    }
    consumer_dead_letters = await db.count("object", consumer_filter)
    consumer_failures = await db.find(
        "object", consumer_filter, limit=100, sort=[("id", 1)]
    )
    dead_letters = await db.count("object", {**base, "context.status": "dead_letter"})
    failed = await db.find(
        "object",
        {**base, "context.status": "dead_letter"},
        limit=100,
        sort=[("context.updated_at", 1), ("id", 1)],
    )
    from app.config import settings

    return {
        "change_events_enabled": settings.CHANGE_EVENT_ENABLED,
        "healthy": settings.CHANGE_EVENT_ENABLED
        and heartbeat_age is not None
        and heartbeat_age <= 30
        and age <= 30
        and dead_letters == 0
        and consumer_dead_letters == 0,
        "heartbeat_age_seconds": heartbeat_age,
        "pending": await db.count("object", pending),
        "dead_letters": dead_letters + consumer_dead_letters,
        "consumer_dead_letters": consumer_dead_letters,
        "consumer_replay_ids": ["consumer:" + row["id"] for row in consumer_failures],
        "dead_letter_events": [
            {
                "id": row["context"]["outbox_id"],
                "attempt": row["context"].get("attempt", 0),
                "error_type": row["context"].get("last_error_type", ""),
            }
            for row in failed
        ],
        "dead_letter_list_truncated": dead_letters > len(failed),
        "oldest_pending_seconds": round(age, 3),
        "heartbeat": heartbeat,
    }


async def replay(outbox_id: str) -> bool:
    """Requeue a dead letter without changing its durable event identity."""
    from app.services.app_operations.event_outbox import event_outbox_object_id
    from app.services.app_operations.transaction_scope import postgres_graph_transaction

    if outbox_id.startswith("consumer:"):
        async with postgres_graph_transaction() as tx:
            result = await tx.find_one_and_update(
                "object",
                {
                    "id": outbox_id[len("consumer:") :],
                    "entity": "ChangeEventTriggerCheckpoint",
                    "context.delivery_status": "dead_letter",
                },
                {
                    "$set": {
                        "context.delivery_status": "pending",
                        "context.delivery_attempt": 0,
                        "context.delivery_next_attempt_at": "",
                        "context.updated_at": utc_now_iso(),
                    }
                },
            )
        return result is not None
    async with postgres_graph_transaction() as tx:
        result = await tx.find_one_and_update(
            "object",
            {"id": event_outbox_object_id(outbox_id), "context.status": "dead_letter"},
            {
                "$set": {
                    "context.status": "pending",
                    "context.attempt": 0,
                    "context.next_attempt_at": "",
                    "context.updated_at": utc_now_iso(),
                }
            },
        )
    return result is not None


async def run(*, once=False) -> None:
    """Drain bounded pages until stopped, retaining failures for recovery."""
    from app.agentive.services.work_events import consume_change_event_page
    from app.services.app_operations.event_outbox import (
        deliver_pending_operation_events,
    )
    from app.services.app_operations.transaction_scope import postgres_graph_transaction

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    fallback_handlers = {}
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            # Windows console event loops do not implement add_signal_handler.
            fallback_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))
    consumers = []
    if not once:
        from functools import partial

        from app.services.event_subscription_registry import registered_consumer_hooks

        consumers.append(
            asyncio.create_task(
                _consumer_loop(partial(consume_change_event_page, limit=100), stop),
                name="event-work-consumer",
            )
        )
        for hook in registered_consumer_hooks():
            consumers.append(
                asyncio.create_task(_consumer_loop(partial(_consume_hook, hook), stop))
            )
    try:
        while not stop.is_set():
            delivered = 0
            try:
                delivered = await deliver_pending_operation_events()
                if once:
                    await consume_change_event_page(limit=100)
                health = await status()
                async with postgres_graph_transaction() as tx:
                    await tx.save(
                        "object",
                        {
                            "id": "o.EventWorkerHeartbeat.events",
                            "entity": "EventWorkerHeartbeat",
                            "context": {
                                "updated_at": utc_now_iso(),
                                "delivered": delivered,
                                "pending": health["pending"],
                                "dead_letters": health["dead_letters"],
                                "oldest_pending_seconds": health[
                                    "oldest_pending_seconds"
                                ],
                            },
                        },
                    )
                if health["dead_letters"] or health["oldest_pending_seconds"] > 30:
                    logger.warning(
                        "Event delivery needs attention pending=%d dead_letters=%d oldest_seconds=%.3f",
                        health["pending"],
                        health["dead_letters"],
                        health["oldest_pending_seconds"],
                    )
                if once:
                    await consume_registered_hooks()
            except Exception:
                logger.warning("Event worker pass failed; retrying", exc_info=False)
                if once:
                    raise
            if once:
                return
            try:
                await asyncio.wait_for(
                    stop.wait(), timeout=1 if delivered < 100 else 0.01
                )
            except asyncio.TimeoutError:
                pass
    finally:
        for consumer in consumers:
            consumer.cancel()
        await asyncio.gather(*consumers, return_exceptions=True)
        for sig in (signal.SIGTERM, signal.SIGINT):
            if sig in fallback_handlers:
                signal.signal(sig, fallback_handlers[sig])
            else:
                loop.remove_signal_handler(sig)


async def main(*, once=False, show_status=False, replay_id=None) -> dict | None:
    """Run a worker or maintenance command and always release its pools."""
    try:
        await initialise(ensure_indexes=not show_status)
        if show_status:
            return await status()
        if replay_id:
            return {"replayed": await replay(replay_id)}
        from app.config import settings

        if not settings.CHANGE_EVENT_ENABLED:
            raise RuntimeError(
                "Enable change events before starting their delivery worker"
            )
        await run(once=once)
        return None
    finally:
        from jvspatial.db import get_database_manager

        manager = get_database_manager()
        seen = set()
        for name in manager.list_databases():
            db = manager.get_database(name)
            if id(db) in seen:
                continue
            seen.add(id(db))
            close = getattr(db, "close", None)
            if close:
                await close()


async def consume_registered_hooks() -> None:
    """Run a bounded pass of each registered consumer (maintenance/tests)."""
    from app.services.event_subscription_registry import registered_consumer_hooks

    for hook in registered_consumer_hooks():
        await _consume_hook(hook)


async def _consume_hook(hook) -> None:
    """Commit a callback's local effects with its own durable checkpoint."""
    import hashlib

    from app.agentive.services.work_events import get_or_create_checkpoint
    from app.services.app_operations.transaction_scope import postgres_graph_transaction
    from app.services.change_event_logger import (
        envelope_from_dblog,
        get_change_event_logger,
    )

    name = (
        "hook_"
        + hashlib.sha256(
            (hook.__module__ + "." + hook.__qualname__).encode()
        ).hexdigest()
    )
    checkpoint = await get_or_create_checkpoint(name)
    # Commit each event independently: a later poison event must not undo the
    # checkpoint or graph effects of callbacks that already completed.
    for _ in range(100):
        context = {}
        try:
            async with postgres_graph_transaction() as tx:
                locked = await tx.find_one_and_update(
                    "object", {"id": checkpoint.id}, {"$set": {"context.locked": True}}
                )
                context = locked["context"]
                if context.get("delivery_status") == "dead_letter":
                    return
                if context.get("delivery_next_attempt_at", "") > utc_now_iso():
                    return
                rows = await get_change_event_logger().find_after_checkpoint(
                    last_logged_at=context.get("last_logged_at"),
                    last_event_id=context.get("last_event_id"),
                    limit=1,
                )
                if not rows:
                    return
                row = rows[0]
                await hook(envelope_from_dblog(row))
                await tx.find_one_and_update(
                    "object",
                    {"id": checkpoint.id},
                    {
                        "$set": {
                            "context.last_logged_at": row.logged_at.isoformat(),
                            "context.last_event_id": row.id,
                            "context.updated_at": utc_now_iso(),
                            "context.delivery_status": "pending",
                            "context.delivery_attempt": 0,
                            "context.delivery_next_attempt_at": "",
                            "context.delivery_error_type": "",
                        }
                    },
                )
        except Exception as exc:
            if not context:
                raise
            # The callback transaction rolled back. Fence retry metadata against
            # a competing worker which may already have completed this event.
            async with postgres_graph_transaction() as tx:
                current = await tx.find_one_and_update(
                    "object", {"id": checkpoint.id}, {"$set": {"context.locked": True}}
                )
                state = current["context"]
                if (state.get("last_logged_at"), state.get("last_event_id")) == (
                    context.get("last_logged_at"),
                    context.get("last_event_id"),
                ):
                    attempt = state.get("delivery_attempt", 0) + 1
                    await tx.find_one_and_update(
                        "object",
                        {"id": checkpoint.id},
                        {
                            "$set": {
                                "context.delivery_attempt": attempt,
                                "context.delivery_status": (
                                    "dead_letter" if attempt >= 5 else "pending"
                                ),
                                "context.delivery_error_type": type(exc).__name__,
                                "context.delivery_next_attempt_at": (
                                    datetime.now(timezone.utc)
                                    + timedelta(seconds=min(60, 2 ** min(attempt, 6)))
                                ).isoformat(),
                                "context.updated_at": utc_now_iso(),
                            }
                        },
                    )
            logger.warning("Event consumer awaits retry", exc_info=False)
            return


async def _consumer_loop(callback, stop: asyncio.Event) -> None:
    """A stalled consumer cannot hold audit delivery or another consumer."""
    while not stop.is_set():
        try:
            from app.services.performance import phase

            with phase("event.consumer"):
                await callback()
        except Exception:
            logger.warning("Event consumer pass awaits retry", exc_info=False)
        try:
            await asyncio.wait_for(stop.wait(), timeout=1)
        except asyncio.TimeoutError:
            pass
