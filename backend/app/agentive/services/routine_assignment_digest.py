"""Deliver new review-ready assignments through the durable routine worker."""

import hashlib
import logging
from urllib.parse import quote, urlencode

from app.agentive.nodes import RoutineTask
from app.agentive.services.routine_task_watch import quiet_hours_end
from app.schemas.agentive.work import WorkError, WorkExecutionContext
from app.utils.time import utc_now

logger = logging.getLogger(__name__)


async def deliver_assignment_digest(
    task: RoutineTask, context: WorkExecutionContext
) -> None:
    """Commit the private digest, deduplication and scan checkpoint together."""
    from app.agentive.services.work_items import authorized_work_item_effect
    from app.api.errors import InsufficientPermissionsError, ResourceNotFoundError
    from app.models.nodes import ChatMessage, ChatThread
    from app.services.app_operations.transaction_scope import (
        current_graph_transaction_database,
    )
    from app.services.chat_thread_events import notify_thread_message
    from app.services.chat_threads import append_message
    from app.services.release_admission import release_admission
    from app.services.resident_assignment import (
        get_resident_assignment,
        get_resident_assignment_review,
        read_declared_task,
    )
    from app.services.resident_opportunities import list_resident_opportunities
    from app.services.resident_opportunity_actions import opportunity_receipt_id

    message_id = (
        "n.ChatMessage."
        + hashlib.sha256(
            f"{context.work_item_id}:assignment_digest".encode()
        ).hexdigest()
    )
    cursor = task.opportunity_cursor
    page = (
        await list_resident_opportunities(
            user_id=context.principal_id,
            workspace_id=context.workspace_id,
            cursor=cursor,
        )
        if task.opportunity_last_work_id != context.work_item_id
        else None
    )
    async with authorized_work_item_effect(context) as graph:
        transaction = current_graph_transaction_database()
        row = await transaction.get("node", task.id)
        fresh = (
            await graph._deserialize_entity(RoutineTask, row)
            if row and row.get("entity") == RoutineTask._entity_name()
            else None
        )
        if (
            fresh is None
            or fresh.delivery_mode != "assignment_digest"
            or fresh.write_scope
        ):
            raise WorkError("work.policy_denied", "Assignment digest is unavailable")
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
                    "work.policy_denied", "Assignment digest destination changed"
                )
            now = utc_now()
            quiet_until = quiet_hours_end(now, fresh.timezone, fresh.quiet_hours)
            if fresh.opportunity_last_work_id == context.work_item_id:
                message = await ChatMessage.get(message_id)
                if quiet_until or message is None or message.thread_id != thread.id:
                    return
            else:
                if page is None or fresh.opportunity_cursor != cursor:
                    raise WorkError(
                        "work.policy_denied", "Assignment scan checkpoint changed"
                    )
                if quiet_until and page["opportunities"]:
                    fresh.watch_delivery_after = quiet_until
                    fresh.opportunity_last_work_id = context.work_item_id
                    await fresh.save()
                    return
                links: list[str] = []
                suggestions: list[dict[str, str]] = []
                for candidate in page["opportunities"]:
                    entry_id = candidate["entry_id"]
                    try:
                        assignment = await get_resident_assignment(
                            user_id=context.principal_id,
                            workspace_id=context.workspace_id,
                            entry_id=entry_id,
                        )
                        if (
                            not assignment.can_request_review
                            or assignment.revision != candidate["assignment_revision"]
                            or await get_resident_assignment_review(
                                user_id=context.principal_id,
                                workspace_id=context.workspace_id,
                                entry_id=entry_id,
                            )
                        ):
                            continue
                        snapshot = await read_declared_task(
                            context.principal_id, context.workspace_id, entry_id
                        )
                    except (InsufficientPermissionsError, ResourceNotFoundError):
                        continue
                    if snapshot is None:
                        continue
                    track_id, fingerprint = snapshot
                    if track_id != candidate["track_id"]:
                        continue
                    receipt_id = opportunity_receipt_id(
                        context.principal_id, context.workspace_id, entry_id
                    )
                    prior = await transaction.get("object", receipt_id)
                    fact = (prior or {}).get("context") or {}
                    if fact.get("fingerprint") == fingerprint:
                        deferred_until = fact.get("deferred_until")
                        if not deferred_until or deferred_until > now.isoformat():
                            continue
                    await transaction.save(
                        "object",
                        {
                            "id": receipt_id,
                            "entity": "ResidentOpportunityDelivery",
                            "context": {
                                "principal_id": context.principal_id,
                                "workspace_id": context.workspace_id,
                                "entry_id": entry_id,
                                "fingerprint": fingerprint,
                                "message_id": message_id,
                            },
                        },
                    )
                    suggestions.append({"entry_id": entry_id, "track_id": track_id})
                    query = urlencode({"entry": entry_id})
                    links.append(
                        f"- [Review assigned task {len(links) + 1}](/tracks/{quote(track_id, safe='')}?{query})"
                    )
                fresh.opportunity_cursor = page.get("next_cursor")
                fresh.opportunity_last_work_id = context.work_item_id
                fresh.watch_delivery_after = None
                await fresh.save()
                if not links:
                    return
                await append_message(
                    thread=thread,
                    role="assistant",
                    parts=[
                        {
                            "type": "text",
                            "text": (
                                "These assignments are ready for a first preparation review. "
                                "Open a task to request preparation.\n\n"
                                + "\n".join(links)
                            ),
                        }
                    ],
                    provider_metadata={
                        "origin": "routine_task",
                        "routine_id": fresh.id,
                        "delivery_mode": "assignment_digest",
                        "assignment_suggestions": suggestions,
                    },
                    message_id=message_id,
                )
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
            "Assignment digest saved but could not notify chat", exc_info=True
        )
