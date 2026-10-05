"""Transactional idempotent acceptance of user-authored chat turns."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.agentive.services.work_items import enqueue_work_item
from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    ResourceConflictError,
)
from app.models.edges import CONTAINS
from app.models.nodes import ChatMessage, ChatThread
from app.schemas.agentive.work import (
    ChatTurnSubmissionReceipt,
    ChatTurnSubmissionRequest,
    WorkError,
)
from app.services.app_operations.transaction_scope import (
    OperationTransactionUnavailable,
    graph_transaction_available,
    postgres_graph_transaction,
)
from app.services.chat_threads import append_message
from app.services.chat_turn_admission import reserve_chat_turn_admission


def _canonical_digest(value: Any) -> str:
    canonical = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _submission_identity(
    *,
    principal_id: str,
    workspace_id: str,
    thread_id: str,
    client_request_id: str,
) -> tuple[str, str]:
    """Return stable scoped WorkItem key and Node id without exposing the key."""
    digest = _canonical_digest(
        {
            "principal_id": principal_id,
            "workspace_id": workspace_id,
            "thread_id": thread_id,
            "client_request_id": client_request_id,
        }
    )
    return f"chat-turn:{digest}", f"n.ChatMessage.{digest}"


def _message_fingerprint(request: ChatTurnSubmissionRequest) -> str:
    return _canonical_digest(
        {
            "parts": request.parts,
            "provider_metadata": request.provider_metadata,
            "parent_id": request.parent_id,
        }
    )


def _request_fingerprint(request: ChatTurnSubmissionRequest) -> str:
    return _canonical_digest(
        {
            "message_fingerprint": _message_fingerprint(request),
            "execution_context": (
                request.execution_context.model_dump(mode="json")
                if request.execution_context is not None
                else None
            ),
        }
    )


async def submit_chat_turn(
    request: ChatTurnSubmissionRequest,
) -> ChatTurnSubmissionReceipt:
    """Accept exactly one scoped user message and durable queue fact.

    The WorkItem identity serializes duplicate requests inside the PostgreSQL
    transaction. Its input stores only the accepted ChatMessage reference and
    a content fingerprint; prompt and attachment bytes remain in the canonical
    encrypted/permissioned chat transcript. The WorkItem and initial outbox
    record are committed atomically with the rooted ChatMessage Node and its
    CONTAINS edge.
    """
    if not graph_transaction_available():
        raise OperationTransactionUnavailable(
            "Chat turn submission requires a transactional shared store"
        )

    idempotency_key, message_id = _submission_identity(
        principal_id=request.principal_id,
        workspace_id=request.workspace_id,
        thread_id=request.thread_id,
        client_request_id=request.client_request_id,
    )
    fingerprint = _request_fingerprint(request)

    async with postgres_graph_transaction() as transaction:
        thread = await ChatThread.get(request.thread_id)
        if (
            thread is None
            or thread.user_id != request.principal_id
            or thread.workspace_id != request.workspace_id
        ):
            raise InsufficientPermissionsError(message="Chat thread scope mismatch")

        if request.parent_id:
            parent = await ChatMessage.get(request.parent_id)
            if parent is None or parent.thread_id != thread.id:
                raise BadRequestError(
                    message="Chat message parent must belong to the same thread"
                )
            context = await thread.get_context()
            parent_edges = await context.find_edges_between(
                thread.id, parent.id, edge_class=CONTAINS
            )
            if not parent_edges:
                raise BadRequestError(
                    message="Chat message parent must belong to the same thread"
                )

        try:
            input_payload = {
                "accepted_message_id": message_id,
                "request_fingerprint": fingerprint,
            }
            work_item = await enqueue_work_item(
                kind="chat_turn",
                origin="interactive_chat",
                principal_id=request.principal_id,
                workspace_id=request.workspace_id,
                thread_id=thread.id,
                idempotency_key=idempotency_key,
                input_payload=input_payload,
                transaction=transaction,
            )
        except WorkError as exc:
            if exc.code == "work.idempotency_conflict":
                raise ResourceConflictError(
                    message="Chat request ID was already used for different content",
                    details={"reason": "chat_submission_idempotency_conflict"},
                ) from exc
            raise

        if request.execution_context is not None:
            from app.agentive.harness.turn_input import persist_turn_input_capsule
            from app.config import settings

            capsule_ref = await persist_turn_input_capsule(
                principal_id=request.principal_id,
                workspace_id=request.workspace_id,
                thread_id=thread.id,
                work_item_id=str(work_item.work_item_id),
                accepted_message_id=message_id,
                client_request_id=request.client_request_id,
                execution_context=request.execution_context,
                retention_days=settings.INTEGRAL_HARNESS_SESSION_RETENTION_DAYS,
            )
            input_payload.update(capsule_ref)
            work_item.input_payload = input_payload
            await work_item.save()

        await reserve_chat_turn_admission(
            transaction=transaction,
            thread=thread,
            principal_id=request.principal_id,
            workspace_id=request.workspace_id,
            work_item=work_item,
        )

        message = await ChatMessage.get(message_id)
        if message is None:
            message = await append_message(
                thread=thread,
                role="user",
                parts=request.parts,
                parent_id=request.parent_id,
                provider_metadata=request.provider_metadata,
                message_id=message_id,
            )
        elif (
            message.thread_id != thread.id
            or message.role != "user"
            or _canonical_digest(
                {
                    "parts": message.parts,
                    "provider_metadata": message.provider_metadata,
                    "parent_id": message.parent_id,
                }
            )
            != _message_fingerprint(request)
        ):
            raise ResourceConflictError(
                message="Accepted chat message failed idempotency validation",
                details={"reason": "chat_submission_message_mismatch"},
            )

        work_item_id = str(work_item.work_item_id)
        return ChatTurnSubmissionReceipt(
            client_request_id=request.client_request_id,
            message_id=message.id,
            work_item_id=work_item_id,
            status=work_item.status,
        )


__all__ = ["submit_chat_turn"]
