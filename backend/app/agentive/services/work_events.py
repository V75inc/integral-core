"""Durable ChangeEvent → WorkItem trigger consumer (Task 9)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from app.agentive.work_models import (
    ChangeEventTriggerCheckpoint,
    EventTriggerDeclaration,
    WorkItem,
)
from app.schemas.agentive.work import WorkError
from app.utils.time import utc_now_iso

log = logging.getLogger(__name__)

DEFAULT_CONSUMER = "work_event_triggers"
DEFAULT_PAGE_SIZE = 50


def event_work_idempotency_key(*, event_id: str, trigger_key: str) -> str:
    return f"event:{event_id}:{trigger_key}"


def checkpoint_object_id(consumer_name: str) -> str:
    return f"o.ChangeEventTriggerCheckpoint.{consumer_name}"


async def get_or_create_checkpoint(
    consumer_name: str = DEFAULT_CONSUMER,
) -> ChangeEventTriggerCheckpoint:
    oid = checkpoint_object_id(consumer_name)
    existing = await ChangeEventTriggerCheckpoint.get(oid)
    if existing is not None:
        return existing
    record, _ = await ChangeEventTriggerCheckpoint.create_if_absent(
        id=oid,
        consumer_name=consumer_name,
        last_logged_at="",
        last_event_id="",
        updated_at=utc_now_iso(),
    )
    return record


async def list_enabled_declarations() -> List[EventTriggerDeclaration]:
    rows = await EventTriggerDeclaration.find({"context.enabled": True})
    return list(rows or [])


def declaration_matches(
    decl: EventTriggerDeclaration,
    *,
    action: str,
    resource_type: str,
    scope: str = "",
) -> bool:
    if (decl.action or "") and decl.action != action:
        return False
    if (decl.resource_type or "") and decl.resource_type != resource_type:
        return False
    prefix = (decl.scope_prefix or "").strip()
    if prefix and not (scope or "").startswith(prefix):
        return False
    return True


async def enqueue_event_trigger(
    *,
    event_id: str,
    trigger_key: str,
    principal_id: str,
    workspace_id: str,
    capability_key: str = "",
    turn_template: str = "",
    action: str = "",
    resource_type: str = "",
    resource_id: str = "",
) -> WorkItem:
    from app.agentive.services import work_items

    if not (capability_key or "").strip() and not (turn_template or "").strip():
        raise WorkError(
            "work.permanent",
            f"trigger {trigger_key} has no capability_key or turn_template",
        )
    kind = "event_trigger" if capability_key else "routine_turn"
    payload: Dict[str, Any] = {
        "trigger_key": trigger_key,
        "event_id": event_id,
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
    }
    if capability_key:
        payload["capability_key"] = capability_key
        payload["source"] = "core"
        payload["op_class"] = "read"
    if turn_template:
        payload["turn_template"] = turn_template
    return await work_items.enqueue_work_item(
        kind=kind,  # type: ignore[arg-type]
        origin="event",
        principal_id=principal_id or "system",
        workspace_id=workspace_id or "",
        idempotency_key=event_work_idempotency_key(
            event_id=event_id, trigger_key=trigger_key
        ),
        input_payload=payload,
        causation_id=event_id,
    )


async def advance_checkpoint(
    checkpoint: ChangeEventTriggerCheckpoint,
    *,
    logged_at: str,
    event_id: str,
) -> ChangeEventTriggerCheckpoint:
    checkpoint.last_logged_at = logged_at
    checkpoint.last_event_id = event_id
    checkpoint.updated_at = utc_now_iso()
    await checkpoint.save()
    return checkpoint


def _logged_at_iso(row: Any) -> str:
    raw = getattr(row, "logged_at", None)
    if isinstance(raw, datetime):
        if raw.tzinfo is None:
            raw = raw.replace(tzinfo=timezone.utc)
        return raw.isoformat()
    return str(raw or "")


async def consume_change_event_page(
    *,
    consumer_name: str = DEFAULT_CONSUMER,
    limit: int = DEFAULT_PAGE_SIZE,
    principal_id: str = "system",
    workspace_id: str = "",
) -> Dict[str, Any]:
    """Read one DBLog page after the checkpoint and enqueue matching triggers.

    Advances the checkpoint only after every match for an event is enqueued
    (or returned as an idempotent winner). Malformed rows fail closed without
    advancing.
    """
    from app.services.change_event_logger import (
        envelope_from_dblog,
        get_change_event_logger,
    )

    checkpoint = await get_or_create_checkpoint(consumer_name)
    logger = get_change_event_logger()
    rows = await logger.find_after_checkpoint(
        last_logged_at=checkpoint.last_logged_at or None,
        last_event_id=checkpoint.last_event_id or None,
        limit=limit,
    )
    declarations = await list_enabled_declarations()
    enqueued = 0
    advanced = 0
    for row in rows:
        event_id = str(getattr(row, "id", "") or "")
        if not event_id:
            raise WorkError("work.permanent", "malformed change event missing id")
        try:
            envelope = envelope_from_dblog(row)
        except Exception as exc:  # noqa: BLE001
            raise WorkError(
                "work.permanent",
                f"malformed change event {event_id}: {exc}",
            ) from exc
        action = str(getattr(envelope, "action", "") or "")
        resource_type = str(getattr(envelope, "resource_type", "") or "")
        scope = str(getattr(envelope, "scope", "") or "")
        resource_id = str(getattr(envelope, "resource_id", "") or "")
        actor_id = str(getattr(envelope, "actor_id", "") or principal_id)
        ws = workspace_id
        if scope.startswith("ws:"):
            ws = scope.split(":", 1)[1].split("/", 1)[0] or ws

        matched = [
            d
            for d in declarations
            if declaration_matches(
                d, action=action, resource_type=resource_type, scope=scope
            )
        ]
        for decl in matched:
            await enqueue_event_trigger(
                event_id=event_id,
                trigger_key=decl.trigger_key,
                principal_id=actor_id or principal_id,
                workspace_id=ws,
                capability_key=decl.capability_key or "",
                turn_template=decl.turn_template or "",
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
            )
            enqueued += 1
        await advance_checkpoint(
            checkpoint,
            logged_at=_logged_at_iso(row),
            event_id=event_id,
        )
        advanced += 1

    return {
        "scanned": len(rows),
        "enqueued": enqueued,
        "advanced": advanced,
        "last_event_id": checkpoint.last_event_id,
        "last_logged_at": checkpoint.last_logged_at,
    }


async def event_consumer_loop(
    *,
    stop_event: Any = None,
    idle_sleep: float = 1.0,
    consumer_name: str = DEFAULT_CONSUMER,
) -> None:
    import asyncio

    stop = stop_event
    while True:
        if stop is not None and stop.is_set():
            return
        try:
            await consume_change_event_page(consumer_name=consumer_name)
        except WorkError as exc:
            log.warning("work_events consumer fail-closed: %s", exc)
            await asyncio.sleep(idle_sleep)
            continue
        except Exception:  # noqa: BLE001
            log.exception("work_events consumer error")
        try:
            if stop is None:
                await asyncio.sleep(idle_sleep)
            else:
                await asyncio.wait_for(stop.wait(), timeout=idle_sleep)
                return
        except asyncio.TimeoutError:
            pass


# Latency hint — event_wake remains non-authoritative.
async def wake_hint_handler(event: Dict[str, Any]) -> None:
    """Best-effort nudge: run one consumer page when substrate changes."""
    try:
        await consume_change_event_page(limit=10)
    except Exception:  # noqa: BLE001
        log.debug("work_events wake hint failed", exc_info=True)


def register_wake_hint() -> None:
    from app.services.event_wake import register_wake_handler

    register_wake_handler(wake_hint_handler)


__all__ = [
    "advance_checkpoint",
    "consume_change_event_page",
    "declaration_matches",
    "enqueue_event_trigger",
    "event_consumer_loop",
    "event_work_idempotency_key",
    "get_or_create_checkpoint",
    "list_enabled_declarations",
    "register_wake_hint",
    "wake_hint_handler",
]
