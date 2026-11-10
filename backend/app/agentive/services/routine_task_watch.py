"""Publish changes to one explicitly reviewed task via a durable routine."""

import hashlib
import logging
from datetime import datetime, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from app.agentive.nodes import RoutineTask
from app.schemas.agentive.routines import RoutineQuietHours
from app.schemas.agentive.work import WorkError, WorkExecutionContext
from app.utils.time import utc_now

logger = logging.getLogger(__name__)


def quiet_hours_end(
    now: datetime, timezone: str, hours: RoutineQuietHours | None
) -> str | None:
    """Find the first allowed UTC minute, including DST gaps and repeated hours."""
    if hours is None:
        return None
    zone = ZoneInfo(timezone)

    def is_quiet(instant: datetime) -> bool:
        wall = instant.astimezone(zone).strftime("%H:%M")
        if hours.start < hours.end:
            return hours.start <= wall < hours.end
        return wall >= hours.start or wall < hours.end

    if not is_quiet(now):
        return None
    # Search actual instants rather than constructing an ambiguous or missing
    # local wall time at a daylight-saving transition.
    minute = now.replace(second=0, microsecond=0)
    for _ in range(48 * 60):
        minute += timedelta(minutes=1)
        if not is_quiet(minute):
            return minute.isoformat()
    raise WorkError("work.policy_denied", "Could not resolve the end of quiet hours")


async def read_task_watch_snapshot(
    user_id: str, workspace_id: str, entry_id: str, *, goal: bool = False
) -> tuple[str, str] | None:
    """Keep a goal watch bound to goal semantics across subsequent checks."""
    if goal:
        from app.services.goal_evidence import read_goal_watch

        snapshot = await read_goal_watch(user_id, workspace_id, entry_id)
        return (snapshot[0], f"goal:{snapshot[1]}") if snapshot else None
    from app.services.resident_assignment import read_declared_task

    return await read_declared_task(user_id, workspace_id, entry_id)


async def deliver_task_watch(task: RoutineTask, context: WorkExecutionContext) -> None:
    """Advance the baseline and publish a private alert in the same fenced write."""
    from app.agentive.services.work_items import authorized_work_item_effect
    from app.api.errors import InsufficientPermissionsError
    from app.models.edges import CONTAINS
    from app.models.nodes import ChatMessage, ChatThread
    from app.services.app_operations.transaction_scope import (
        current_graph_transaction_database,
    )
    from app.services.chat_thread_events import notify_thread_message
    from app.services.chat_threads import append_message
    from app.services.release_admission import release_admission

    async with authorized_work_item_effect(context) as graph:
        transaction = current_graph_transaction_database()
        routine_row = await transaction.lock_record("node", task.id)
        fresh = (
            await graph._deserialize_entity(RoutineTask, routine_row)
            if routine_row and routine_row.get("entity") == RoutineTask._entity_name()
            else None
        )
        if fresh is None or fresh.delivery_mode != "task_watch" or fresh.write_scope:
            raise WorkError("work.policy_denied", "Task watch is no longer available")
        try:
            async with release_admission(context.principal_id, context.workspace_id):
                row = await transaction.lock_record("node", context.thread_id)
                thread = (
                    await graph._deserialize_entity(ChatThread, row)
                    if row and row.get("entity") == ChatThread._entity_name()
                    else None
                )
                if (
                    thread is None
                    or thread.archived
                    or thread.provider_id != "integral_native"
                    or thread.user_id != context.principal_id
                    or thread.workspace_id != context.workspace_id
                ):
                    raise WorkError(
                        "work.policy_denied", "Task watch destination changed"
                    )
                goal = fresh.watch_fingerprint.startswith("goal:")
                snapshot = await read_task_watch_snapshot(
                    context.principal_id,
                    context.workspace_id,
                    fresh.watch_entry_id,
                    goal=goal,
                )
                if snapshot is None:
                    raise WorkError(
                        "work.policy_denied", "Watched task is no longer readable"
                    )
                track_id, fingerprint = snapshot
                message_id = (
                    "n.ChatMessage."
                    + hashlib.sha256(
                        f"{context.work_item_id}:task_watch".encode()
                    ).hexdigest()
                )
                existing = await ChatMessage.get(message_id)
                delivery_after = quiet_hours_end(
                    utc_now(), fresh.timezone, fresh.quiet_hours
                )
                if existing is not None:
                    if (
                        existing.thread_id != thread.id
                        or (existing.provider_metadata or {}).get("routine_id")
                        != fresh.id
                        or not await graph.find_edges_between(
                            thread.id, message_id, edge_class=CONTAINS
                        )
                    ):
                        raise WorkError(
                            "work.idempotency_conflict", "Task watch delivery differs"
                        )
                    # A retry can refresh the same persisted message outside quiet hours.
                    if delivery_after:
                        return
                elif fingerprint == fresh.watch_fingerprint:
                    if fresh.watch_delivery_after:
                        fresh.watch_delivery_after = None
                        await fresh.save()
                    return
                elif delivery_after:
                    if fresh.watch_delivery_after != delivery_after:
                        fresh.watch_delivery_after = delivery_after
                        await fresh.save()
                    return
                else:
                    query = urlencode({"entry": fresh.watch_entry_id})
                    await append_message(
                        thread=thread,
                        role="assistant",
                        parts=[
                            {
                                "type": "text",
                                "text": (
                                    f"Your [watched {'goal' if goal else 'task'}](/tracks/{track_id}?{query}) changed. "
                                    "Open it to review the current details."
                                ),
                            }
                        ],
                        provider_metadata={
                            "origin": "routine_task",
                            "routine_id": fresh.id,
                            "delivery_mode": "task_watch",
                        },
                        message_id=message_id,
                    )
                    fresh.watch_fingerprint = fingerprint
                    fresh.watch_delivery_after = None
                    await fresh.save()
        except InsufficientPermissionsError as exc:
            raise WorkError(
                "work.policy_denied", "Task watch workspace access changed"
            ) from exc
    try:
        await notify_thread_message(
            context.principal_id,
            context.thread_id,
            message_id=message_id,
            role="assistant",
            origin="routine_task",
        )
    except Exception:
        logger.warning(
            "Task watch alert persisted but could not notify chat", exc_info=True
        )
