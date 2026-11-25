"""Shared cancellation of an already-resolved, authorized chat work item."""

import logging

from app.api.errors import (
    InternalServerError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from app.schemas.agentive.chat_events import ChatTurnCancellationReceipt
from app.schemas.agentive.work import WorkError
from app.services import chat_turn_registry

logger = logging.getLogger(__name__)


async def cancel_owned_chat_work(
    item, *, user_id, workspace_id, thread_id, preserve_human_review=False
):
    """Apply the canonical cancellation and receipt to an exact owned WorkItem."""
    if (
        item.kind != "chat_turn"
        or item.principal_id != user_id
        or item.workspace_id != workspace_id
        or item.thread_id != thread_id
    ):
        raise ResourceNotFoundError(message="Chat turn not found")
    from app.agentive.services.work_items import cancel_work_item

    before = {
        "status": item.status,
        "cancel_requested": bool(item.cancel_requested_at),
    }
    try:
        cancel_options = (
            {"preserve_human_review": True} if preserve_human_review else {}
        )
        cancelled = await cancel_work_item(item.work_item_id, **cancel_options)
        if preserve_human_review and cancelled.status == "waiting_for_human":
            raise ResourceConflictError(message="This task is awaiting review.")
    except WorkError as exc:
        if exc.code == "work.not_found":
            raise ResourceNotFoundError(message="Chat turn not found") from exc
        if exc.code in {"work.invalid_transition", "work.cas_conflict"}:
            raise ResourceConflictError(
                message="Chat turn changed while cancellation was requested",
                details={"reason": "chat_turn_state_changed"},
            ) from exc
        logger.exception("Failed to cancel chat WorkItem %s", item.work_item_id)
        raise InternalServerError(message="Could not cancel chat turn") from exc
    # Wake the local provider immediately; the persisted request remains the
    # cross-worker authority and is also observed by the lease supervisor.
    await chat_turn_registry.cancel_turn_if_work_item(thread_id, cancelled.work_item_id)
    from app.services.change_event import emit_change_event

    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="chat_turn.cancel",
        resource_type="WorkItem",
        resource_id=cancelled.work_item_id,
        scope=workspace_id,
        before=before,
        after={
            "status": cancelled.status,
            "cancel_requested": bool(cancelled.cancel_requested_at),
        },
        details={"thread_id": thread_id},
    )
    receipt = ChatTurnCancellationReceipt(
        work_item_id=cancelled.work_item_id,
        status=cancelled.status,
        cancel_requested=bool(cancelled.cancel_requested_at),
    )
    return receipt.model_dump()
