"""Transactional idempotent acceptance of user-authored chat turns."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from app.agentive.services.work_items import enqueue_work_item
from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    ResourceConflictError,
)
from app.config import settings
from app.models.edges import CONTAINS
from app.models.nodes import ChatMessage, ChatThread
from app.schemas.agentive.work import (
    ChatTurnExecutionContext,
    ChatTurnSubmissionReceipt,
    ChatTurnSubmissionRequest,
    WorkError,
)
from app.services.app_operations.transaction_scope import (
    OperationTransactionUnavailable,
    graph_transaction_available,
    postgres_graph_transaction,
)
from app.services.chat_threads import append_message, derive_thread_title
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
    context = (
        request.execution_context.model_dump(mode="json")
        if request.execution_context is not None
        else None
    )
    # Preserve retained pre-attachment capsule fingerprints. An empty binding
    # field carries no additional authority or input revision.
    if context is not None and not context.get("attachment_bindings"):
        context.pop("attachment_bindings", None)
    return _canonical_digest(
        {
            "message_fingerprint": _message_fingerprint(request),
            "execution_context": context,
        }
    )


def assert_accepted_chat_turn_fingerprint(
    *,
    request: ChatTurnSubmissionRequest,
    expected_fingerprint: str,
) -> None:
    """Bind restored canonical message content to its accepted request.

    An omitted host context was historically fingerprinted as null, while its
    encrypted capsule stores the typed empty context. Accept that equivalent
    representation only when the restored context is entirely empty.
    """
    fingerprints = {_request_fingerprint(request)}
    if request.execution_context == ChatTurnExecutionContext():
        fingerprints.add(
            _request_fingerprint(request.model_copy(update={"execution_context": None}))
        )
    if not expected_fingerprint or expected_fingerprint not in fingerprints:
        raise WorkError("work.policy_denied", "accepted chat input changed")


async def get_chat_turn_submission_receipt(
    *,
    principal_id: str,
    workspace_id: str,
    thread_id: str,
    client_request_id: str,
    client_payload_digest: str,
) -> ChatTurnSubmissionReceipt | None:
    """Recover accepted HTTP intent before rebuilding live host/file context."""
    from app.agentive.services.work_items import work_item_object_id
    from app.agentive.work_models import WorkItem

    key, message_id = _submission_identity(
        principal_id=principal_id,
        workspace_id=workspace_id,
        thread_id=thread_id,
        client_request_id=client_request_id,
    )
    item = await WorkItem.get(
        work_item_object_id(
            kind="chat_turn",
            origin="interactive_chat",
            principal_id=principal_id,
            workspace_id=workspace_id,
            idempotency_key=key,
        )
    )
    if item is None:
        return None
    thread = await ChatThread.get(thread_id)
    if (
        thread is None
        or thread.user_id != principal_id
        or thread.workspace_id != workspace_id
        or item.principal_id != principal_id
        or item.workspace_id != workspace_id
        or item.thread_id != thread_id
        or item.kind != "chat_turn"
    ):
        raise InsufficientPermissionsError(message="Chat thread scope mismatch")
    payload = dict(item.input_payload or {})
    if payload.get("client_payload_digest") != client_payload_digest:
        raise ResourceConflictError(
            message="Chat request ID was already used for different content",
            details={"reason": "chat_submission_idempotency_conflict"},
        )
    message = await ChatMessage.get(message_id)
    if (
        not payload.get("capsule_id")
        or not payload.get("capsule_digest")
        or payload.get("accepted_message_id") != message_id
        or message is None
        or message.thread_id != thread_id
        or message.role != "user"
        or payload.get("message_fingerprint")
        != _canonical_digest(
            {
                "parts": message.parts,
                "provider_metadata": message.provider_metadata,
                "parent_id": message.parent_id,
            }
        )
    ):
        raise ResourceConflictError(
            message="Accepted chat message failed idempotency validation",
            details={"reason": "chat_submission_message_mismatch"},
        )
    graph = await thread.get_context()
    if not await graph.find_edges_between(thread_id, message_id, edge_class=CONTAINS):
        raise ResourceConflictError(
            message="Accepted chat message is detached",
            details={"reason": "chat_submission_message_mismatch"},
        )
    return ChatTurnSubmissionReceipt(
        client_request_id=client_request_id,
        message_id=message_id,
        work_item_id=item.work_item_id,
        status=item.status,
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
    deadline_at = (
        datetime.now(timezone.utc)
        + timedelta(seconds=settings.INTEGRAL_HARNESS_CHAT_TURN_TIMEOUT_SECONDS)
    ).isoformat()

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
            if request.client_payload_digest:
                # HTTP retries bind the same client intent while retaining the
                # first accepted host snapshot. Current host state may evolve
                # before a lost acceptance response is retried.
                input_payload = {
                    "accepted_message_id": message_id,
                    "client_payload_digest": request.client_payload_digest,
                    "message_fingerprint": _message_fingerprint(request),
                }
            work_item = await enqueue_work_item(
                kind="chat_turn",
                origin="interactive_chat",
                principal_id=request.principal_id,
                workspace_id=request.workspace_id,
                thread_id=thread.id,
                idempotency_key=idempotency_key,
                input_payload=input_payload,
                deadline_at=deadline_at,
                transaction=transaction,
            )
        except WorkError as exc:
            if exc.code == "work.idempotency_conflict":
                raise ResourceConflictError(
                    message="Chat request ID was already used for different content",
                    details={"reason": "chat_submission_idempotency_conflict"},
                ) from exc
            raise

        # Enqueue serializes this identity. A complete prior acceptance is a
        # receipt lookup, not another admission or capsule write. In particular,
        # a completed turn must not touch a newer active turn or depend on the
        # encryption service being available just to recover its public receipt.
        accepted_payload = dict(work_item.input_payload or {})
        if accepted_payload.get("capsule_id") or accepted_payload.get("capsule_digest"):
            message = await ChatMessage.get(message_id)
            if (
                not accepted_payload.get("capsule_id")
                or not accepted_payload.get("capsule_digest")
                or accepted_payload.get("accepted_message_id") != message_id
                or (
                    accepted_payload.get("client_payload_digest")
                    != request.client_payload_digest
                    if request.client_payload_digest
                    else accepted_payload.get("request_fingerprint") != fingerprint
                )
                or message is None
                or message.thread_id != thread.id
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
            graph = await thread.get_context()
            if not await graph.find_edges_between(
                thread.id, message.id, edge_class=CONTAINS
            ):
                raise ResourceConflictError(
                    message="Accepted chat message is detached",
                    details={"reason": "chat_submission_message_mismatch"},
                )
            return ChatTurnSubmissionReceipt(
                client_request_id=request.client_request_id,
                message_id=message.id,
                work_item_id=str(work_item.work_item_id),
                status=work_item.status,
            )

        from app.services.chat_turn_attachments import assert_chat_attachment_bindings

        await assert_chat_attachment_bindings(
            thread=thread,
            principal_id=request.principal_id,
            parts=request.parts,
            expected=(
                request.execution_context.attachment_bindings
                if request.execution_context
                else []
            ),
        )
        # Every accepted durable turn needs a restorable encrypted capsule,
        # even when the host has no extra context to bind. An omitted context
        # is represented by the empty, typed context rather than a capsule-less
        # WorkItem that the worker cannot safely reconstruct after restart.
        from app.agentive.harness.turn_input import persist_turn_input_capsule

        capsule_ref = await persist_turn_input_capsule(
            principal_id=request.principal_id,
            workspace_id=request.workspace_id,
            thread_id=thread.id,
            work_item_id=str(work_item.work_item_id),
            accepted_message_id=message_id,
            client_request_id=request.client_request_id,
            execution_context=(request.execution_context or ChatTurnExecutionContext()),
            retention_days=settings.INTEGRAL_HARNESS_SESSION_RETENTION_DAYS,
        )
        input_payload["request_fingerprint"] = fingerprint
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

        # Apply ordinary first-turn presentation only after admission succeeds,
        # in the same acceptance transaction. Receipt retries return above.
        if not thread.title:
            title_text = " ".join(
                str(part.get("text") or "")
                for part in request.parts
                if part.get("type") == "text"
            )
            thread.title = derive_thread_title(title_text)
        thread.pending_question = None
        if request.execution_context and request.execution_context.extra_data.get(
            "page_context"
        ):
            thread.last_page_context = request.execution_context.extra_data[
                "page_context"
            ]
        await thread.save()

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


__all__ = ["assert_accepted_chat_turn_fingerprint", "submit_chat_turn"]
