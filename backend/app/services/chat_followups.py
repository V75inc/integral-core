"""Durable acceptance of native chat follow-ups, scoped to the thread owner."""

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from jvspatial.core.context import get_default_context
from pydantic import TypeAdapter

from app.agentive.harness.turn_input import thread_scope_key
from app.api.errors import (
    OperationTransactionUnavailableError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from app.config import settings
from app.models.harness_records import HarnessFollowUpInbox
from app.models.nodes import ChatMessage, ChatThread
from app.schemas.agentive.chat_followups import ChatFollowUpRequest, FollowUpStatus
from app.schemas.agentive.work import (
    ChatTurnExecutionContext,
    ChatTurnSubmissionReceipt,
    ChatTurnSubmissionRequest,
)
from app.services.app_operations.transaction_scope import (
    graph_transaction_available,
    postgres_graph_transaction,
)
from app.services.chat_turn_submissions import _submission_identity
from app.services.credential_crypto import (
    decrypt_secret_from_storage,
    encrypt_secret_for_storage,
)
from app.services.workspace_permissions import can_access_workspace

_MAX_PENDING = 16
_FOLLOW_UPS = TypeAdapter(list[ChatFollowUpRequest])


async def _owned_scope(*, principal_id: str, workspace_id: str, thread_id: str) -> str:
    thread = await ChatThread.get(thread_id)
    if (
        thread is None
        or thread.archived
        or thread.user_id != principal_id
        or thread.workspace_id != workspace_id
        or thread.provider_id != "integral_native"
        or await can_access_workspace(principal_id, workspace_id) == "none"
    ):
        raise ResourceNotFoundError(message="Native chat thread not found")
    return thread_scope_key(
        principal_id=principal_id, workspace_id=workspace_id, thread_id=thread_id
    )


def _inbox_id(scope_key: str) -> str:
    return f"o.HarnessFollowUpInbox.{scope_key}"


def _decode(inbox_id: str, ciphertext: str) -> list[ChatFollowUpRequest]:
    return _FOLLOW_UPS.validate_json(
        decrypt_secret_from_storage(ciphertext, aad=inbox_id)
    )


def _disposition_id(scope_key: str, client_request_id: str) -> str:
    digest = hashlib.sha256(client_request_id.encode()).hexdigest()
    return f"{_inbox_id(scope_key)}-receipt-{digest}"


async def _lock_pending(
    transaction: Any, scope_key: str
) -> tuple[list[ChatFollowUpRequest], str]:
    inbox_id = _inbox_id(scope_key)
    row = await transaction.find_one_and_update(
        "object",
        {"id": inbox_id, "context.scope_key": scope_key},
        {"$set": {}},
    )
    if row is None:
        return [], ""
    expires_at = row["context"]["expires_at"]
    if expires_at <= datetime.now(timezone.utc).isoformat():
        return [], ""
    return _decode(inbox_id, row["context"]["payload_ciphertext"]), expires_at


async def _save_pending(
    transaction: Any, scope_key: str, pending: list[ChatFollowUpRequest]
) -> None:
    inbox_id = _inbox_id(scope_key)
    ciphertext = encrypt_secret_for_storage(
        _FOLLOW_UPS.dump_json(pending).decode(), aad=inbox_id
    )
    await transaction.find_one_and_update(
        "object",
        {"id": inbox_id},
        {
            "$set": {
                "context.payload_ciphertext": ciphertext,
                "context.has_pending": bool(pending),
                **(
                    {"context.resumed_after_interruption": False} if not pending else {}
                ),
            }
        },
    )


async def accept_chat_follow_up(
    *,
    principal_id: str,
    workspace_id: str,
    thread_id: str,
    request: ChatFollowUpRequest,
) -> FollowUpStatus:
    """Commit a FIFO input independently of worker or browser lifetime."""
    if not graph_transaction_available():
        raise OperationTransactionUnavailableError()
    async with postgres_graph_transaction() as transaction:
        scope_key = await _owned_scope(
            principal_id=principal_id, workspace_id=workspace_id, thread_id=thread_id
        )
        predecessor_status = ""
        if request.after_work_item_id:
            from app.agentive.work_models import WorkItem

            predecessor = await transaction.find_one_and_update(
                "object",
                {
                    "id": f"o.WorkItem.{request.after_work_item_id}",
                    "entity": WorkItem._entity_name(),
                    "context.principal_id": principal_id,
                    "context.workspace_id": workspace_id,
                    "context.thread_id": thread_id,
                    "context.kind": "chat_turn",
                    "context.origin": "interactive_chat",
                },
                {"$set": {}},
            )
            if predecessor is None:
                raise ResourceNotFoundError(message="Chat turn not found")
            predecessor_status = predecessor["context"]["status"]

        inbox_id = _inbox_id(scope_key)
        now = datetime.now(timezone.utc)
        await HarnessFollowUpInbox.create_if_absent(
            id=inbox_id,
            scope_key=scope_key,
            thread_id=thread_id,
            expires_at=(
                now + timedelta(days=settings.INTEGRAL_HARNESS_SESSION_RETENTION_DAYS)
            ).isoformat(),
            payload_ciphertext=encrypt_secret_for_storage("[]", aad=inbox_id),
        )
        pending, expires_at = await _lock_pending(transaction, scope_key)
        if not expires_at:
            raise ResourceConflictError(message="Follow-up queue expired")
        receipt_id = _disposition_id(scope_key, request.client_request_id)
        receipt = await transaction.get("object", receipt_id)
        if receipt is not None:
            if _decode(receipt_id, receipt["context"]["payload_ciphertext"]) != [
                request
            ]:
                raise ResourceConflictError(
                    message="Follow-up request ID already used for different content"
                )
            return (
                "dismissed"
                if receipt["context"]["disposition"] == "dismissed"
                else "steered"
            )
        for prior in pending:
            if prior.client_request_id == request.client_request_id:
                if prior != request:
                    raise ResourceConflictError(
                        message="Follow-up request ID already used for different content"
                    )
                return "queued"
        # Promotion writes the canonical message in the same transaction that
        # removes the head. The inbox lock makes that receipt visible to retries.
        _, message_id = _submission_identity(
            principal_id=principal_id,
            workspace_id=workspace_id,
            thread_id=thread_id,
            client_request_id=request.client_request_id,
            from_follow_up_queue=True,
        )
        message = await ChatMessage.get(message_id)
        if message is not None:
            if (
                message.thread_id != thread_id
                or message.role != "user"
                or message.parent_id is not None
                or not _matches_content(
                    request, message.parts, message.provider_metadata
                )
            ):
                raise ResourceConflictError(
                    message="Follow-up request ID already used for different content"
                )
            return "promoted"
        if len(pending) >= _MAX_PENDING:
            raise ResourceConflictError(message="Follow-up queue is full")
        await _save_pending(transaction, scope_key, [*pending, request])
        if predecessor_status in {"failed", "cancelled", "expired", "dead_letter"}:
            await pause_follow_ups_after_failure(
                transaction=transaction,
                principal_id=principal_id,
                workspace_id=workspace_id,
                thread_id=thread_id,
            )
        return "queued"


async def list_chat_follow_ups(
    *, principal_id: str, workspace_id: str, thread_id: str
) -> list[ChatFollowUpRequest]:
    """Read accepted inputs in order after rechecking current access."""
    scope_key = await _owned_scope(
        principal_id=principal_id, workspace_id=workspace_id, thread_id=thread_id
    )
    inbox_id = _inbox_id(scope_key)
    inbox = await get_default_context().database.get("object", inbox_id)
    if (
        inbox is None
        or inbox["context"]["expires_at"] <= datetime.now(timezone.utc).isoformat()
    ):
        return []
    return _decode(inbox_id, inbox["context"]["payload_ciphertext"])


def _matches_content(
    follow_up: ChatFollowUpRequest,
    parts: list[dict[str, Any]],
    provider_metadata: dict[str, Any],
) -> bool:
    return parts == [
        {"type": "text", "text": follow_up.text}
    ] and provider_metadata.get("entity_refs", []) == [
        ref.model_dump(mode="json") for ref in follow_up.entity_refs
    ]


async def consume_chat_follow_up(
    *, transaction: Any, submission: ChatTurnSubmissionRequest
) -> None:
    """Remove the matching head only within its new turn's acceptance transaction."""
    scope_key = await _owned_scope(
        principal_id=submission.principal_id,
        workspace_id=submission.workspace_id,
        thread_id=submission.thread_id,
    )
    pending, _ = await _lock_pending(transaction, scope_key)
    inbox = await transaction.get("object", _inbox_id(scope_key))
    if inbox and inbox["context"].get("paused", False):
        raise ResourceConflictError(message="Follow-up queue is paused")
    if not pending or pending[0].client_request_id != submission.client_request_id:
        raise ResourceConflictError(
            message="Follow-up is no longer at the front of the queue"
        )

    if (
        not _matches_content(pending[0], submission.parts, submission.provider_metadata)
        or submission.parent_id
        or (submission.execution_context and submission.execution_context.host_control)
    ):
        raise ResourceConflictError(
            message="Turn content differs from the queued follow-up"
        )

    await _save_pending(transaction, scope_key, pending[1:])


async def convert_chat_follow_up_to_steering(
    *,
    transaction: Any,
    principal_id: str,
    workspace_id: str,
    thread_id: str,
    client_request_id: str,
    text: str,
) -> None:
    """Transfer a selected text-only input inside steering acceptance."""
    scope_key = await _owned_scope(
        principal_id=principal_id, workspace_id=workspace_id, thread_id=thread_id
    )
    pending, expires_at = await _lock_pending(transaction, scope_key)
    for index, follow_up in enumerate(pending):
        if follow_up.client_request_id != client_request_id:
            continue
        if follow_up.text != text or follow_up.entity_refs:
            raise ResourceConflictError(
                message="Only matching text-only follow-ups can be steered"
            )
        await _settle_pending(
            transaction, scope_key, pending, index, expires_at, "steered"
        )
        return
    raise ResourceConflictError(message="Follow-up is no longer queued")


async def _settle_pending(
    transaction: Any,
    scope_key: str,
    pending: list[ChatFollowUpRequest],
    index: int,
    expires_at: str,
    disposition: Literal["steered", "dismissed"],
) -> None:
    follow_up = pending[index]
    receipt_id = _disposition_id(scope_key, follow_up.client_request_id)
    await HarnessFollowUpInbox.create_if_absent(
        id=receipt_id,
        scope_key=scope_key,
        expires_at=expires_at,
        disposition=disposition,
        payload_ciphertext=encrypt_secret_for_storage(
            _FOLLOW_UPS.dump_json([follow_up]).decode(), aad=receipt_id
        ),
    )
    await _save_pending(transaction, scope_key, pending[:index] + pending[index + 1 :])


async def dismiss_chat_follow_up(
    *, principal_id: str, workspace_id: str, thread_id: str, client_request_id: str
) -> None:
    """Dismiss a pending follow-up with a durable receipt."""
    if not graph_transaction_available():
        raise OperationTransactionUnavailableError()
    async with postgres_graph_transaction() as transaction:
        scope_key = await _owned_scope(
            principal_id=principal_id, workspace_id=workspace_id, thread_id=thread_id
        )
        pending, expires_at = await _lock_pending(transaction, scope_key)
        for index, follow_up in enumerate(pending):
            if follow_up.client_request_id == client_request_id:
                await _settle_pending(
                    transaction, scope_key, pending, index, expires_at, "dismissed"
                )
                return
        receipt = await transaction.get(
            "object", _disposition_id(scope_key, client_request_id)
        )
        if receipt is None or receipt["context"]["disposition"] != "dismissed":
            raise ResourceConflictError(message="Follow-up is no longer queued")


async def pause_follow_ups_after_failure(
    *, transaction: Any, principal_id: str, workspace_id: str, thread_id: str
) -> None:
    """Keep pending input for user review when the preceding turn fails."""
    scope_key = thread_scope_key(
        principal_id=principal_id, workspace_id=workspace_id, thread_id=thread_id
    )
    await transaction.find_one_and_update(
        "object",
        {"id": _inbox_id(scope_key), "context.has_pending": True},
        {"$set": {"context.paused": True}},
    )


async def resume_chat_follow_ups(
    *, principal_id: str, workspace_id: str, thread_id: str
) -> None:
    """Resume the queue after an interrupted turn."""
    if not graph_transaction_available():
        raise OperationTransactionUnavailableError()
    async with postgres_graph_transaction() as transaction:
        scope_key = await _owned_scope(
            principal_id=principal_id, workspace_id=workspace_id, thread_id=thread_id
        )
        pending, expires_at = await _lock_pending(transaction, scope_key)
        inbox = await transaction.get("object", _inbox_id(scope_key))
        if not pending or not expires_at or inbox is None:
            raise ResourceConflictError(message="There are no follow-ups to resume")
        fields = inbox["context"]
        if not fields.get("paused", False):
            if fields.get("resumed_after_interruption", False):
                return
            raise ResourceConflictError(message="Follow-up queue is not paused")
        await transaction.find_one_and_update(
            "object",
            {"id": _inbox_id(scope_key)},
            {
                "$set": {
                    "context.paused": False,
                    "context.resumed_after_interruption": True,
                }
            },
        )


async def chat_follow_ups_paused(
    *, principal_id: str, workspace_id: str, thread_id: str
) -> bool:
    """Return whether pending follow-ups need user review."""
    scope_key = await _owned_scope(
        principal_id=principal_id, workspace_id=workspace_id, thread_id=thread_id
    )
    inbox = await get_default_context().database.get("object", _inbox_id(scope_key))
    return bool(inbox and inbox["context"].get("paused", False))


async def promote_next_chat_follow_up(
    cursor: dict[str, str],
) -> ChatTurnSubmissionReceipt | None:
    """Give an eligible inbox one admission attempt per bounded scan page."""
    if (
        not settings.INTEGRAL_NATIVE_DURABLE_CHAT_ENABLED
        or not graph_transaction_available()
    ):
        return None

    from app.services.chat_entity_refs import (
        entities_referenced_payload,
        focused_ids_from_resolved,
        resolve_entity_refs,
    )
    from app.services.chat_page_context import wrap_injected_context
    from app.services.chat_turn_submissions import submit_chat_turn

    query: dict[str, Any] = {
        "entity": HarnessFollowUpInbox._entity_name(),
        "context.has_pending": True,
        "context.expires_at": {"$gt": datetime.now(timezone.utc).isoformat()},
    }
    if cursor.get("id"):
        query["id"] = {"$gt": cursor["id"]}
    rows = await get_default_context().database.find(
        "object", query, sort=[("id", 1)], limit=16
    )
    if not rows:
        cursor.clear()
    for row in rows:
        cursor["id"] = row["id"]
        fields = row["context"]
        if fields.get("paused", False):
            continue
        thread = await ChatThread.get(fields["thread_id"])
        if thread is None or thread.active_work_item_id:
            continue
        scope = {
            "principal_id": thread.user_id,
            "workspace_id": thread.workspace_id,
            "thread_id": thread.id,
        }
        try:
            if await _owned_scope(**scope) != fields["scope_key"]:
                continue
            pending = _decode(row["id"], fields["payload_ciphertext"])
            if not pending:
                continue
            head = pending[0]
            refs = await resolve_entity_refs(
                head.text, head.entity_refs, thread.user_id, thread.workspace_id
            )
            track_id, app_id = focused_ids_from_resolved(refs.resolved)
            extra_data: dict[str, Any] = {}
            if thread.agent_id:
                extra_data["agent_id"] = thread.agent_id
            if refs.resolved:
                extra_data["entities_referenced"] = entities_referenced_payload(
                    refs.resolved
                )
            context_blocks = [
                wrap_injected_context("entity_refs", refs.context_preamble or "")
            ]
            if fields.get("resumed_after_interruption", False):
                context_blocks.append(
                    "[SYSTEM:FOLLOW-UP-AFTER-INTERRUPTION]\n"
                    "The immediately preceding reply was interrupted before "
                    "completion. This status applies only to that reply. "
                    "The user reviewed the interruption and chose to continue "
                    "the queued message. Do not assume that interrupted task finished "
                    "or that its proposed actions happened. Answer the queued "
                    "message using available evidence, and state any important "
                    "missing context or evidence. Treat the queued message as "
                    "the current request. Follow its requested length and format. "
                    "Do not complete the interrupted request unless needed to "
                    "answer the current message."
                )
            return await submit_chat_turn(
                ChatTurnSubmissionRequest(
                    **scope,
                    client_request_id=head.client_request_id,
                    parts=[{"type": "text", "text": head.text}],
                    provider_metadata={
                        "entity_refs": [
                            ref.model_dump(mode="json") for ref in head.entity_refs
                        ]
                    },
                    execution_context=ChatTurnExecutionContext(
                        system_context="\n\n".join(
                            block for block in context_blocks if block
                        ),
                        focused_track_id=track_id,
                        focused_space_id=app_id,
                        extra_data=extra_data,
                    ),
                    from_follow_up_queue=True,
                )
            )
        except (ResourceConflictError, ResourceNotFoundError):
            # A competing worker/foreground turn or live scope change won.
            # Admission and inbox consumption roll back together.
            continue
    return None
