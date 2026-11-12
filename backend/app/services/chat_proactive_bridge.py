"""Bridge proactive delivery to an explicitly scoped Integral ChatThread."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional

from jvspatial.core.context import get_default_context

from app.agentive.harness.turn_input import thread_scope_key
from app.api.errors import (
    BadRequestError,
    OperationTransactionUnavailableError,
    ResourceConflictError,
)
from app.models.edges import CONTAINS
from app.models.harness_records import HarnessProactiveDeliveryRecord
from app.models.nodes import ChatMessage, ChatThread
from app.services import chat_threads as chat_store
from app.services.chat_thread_events import notify_thread_message
from app.services.release_admission import release_admission
from app.services.workspace_permissions import can_access_workspace
from app.utils.time import utc_now_iso


async def find_thread_by_provider_session(
    *,
    user_id: str,
    workspace_id: str,
    provider_session_id: str,
) -> Optional[ChatThread]:
    """Find exactly one active thread in the authorized caller workspace."""
    if not user_id or not workspace_id or not provider_session_id:
        return None
    if await can_access_workspace(user_id, workspace_id) == "none":
        return None
    native_id = provider_session_id.startswith("n.ChatThread.")
    if native_id:
        native = await ChatThread.get(provider_session_id)
        threads = [native] if native is not None else []
    else:
        threads = await get_default_context().find(
            ChatThread,
            {
                "context.user_id": user_id,
                "context.workspace_id": workspace_id,
                "context.provider_session_id": provider_session_id,
                "context.archived": False,
            },
            limit=2,
        )
    if len(threads) != 1:
        return None
    thread = threads[0]
    if not _matches_binding(thread, user_id, workspace_id, provider_session_id):
        return None
    return thread


def _matches_binding(
    thread: ChatThread, user_id: str, workspace_id: str, session_id: str
) -> bool:
    """Validate the full hydrated destination, including native thread IDs."""
    if (
        thread.user_id != user_id
        or thread.workspace_id != workspace_id
        or thread.archived
    ):
        return False
    if session_id.startswith("n.ChatThread."):
        return thread.id == session_id and thread.provider_id == "integral_native"
    return thread.provider_session_id == session_id


async def persist_proactive_message_on_thread(
    *,
    user_id: str,
    workspace_id: str,
    session_id: str,
    content: str,
    delivery_id: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Deliver only to an exact session in the caller's active workspace.

    Missing or ambiguous bindings are refused; no other conversation is a
    substitute. The caller must resolve workspace identity from authenticated
    request state rather than a model or connector payload.
    """
    if not user_id or not workspace_id or not session_id:
        return None
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", delivery_id or ""):
        raise BadRequestError(
            message="A valid stable proactive delivery ID is required"
        )

    from app.services.app_operations.transaction_scope import (
        current_graph_transaction_database,
        graph_transaction_available,
    )

    if (
        await find_thread_by_provider_session(
            user_id=user_id, workspace_id=workspace_id, provider_session_id=session_id
        )
        is None
    ):
        return None
    if not graph_transaction_available():
        raise OperationTransactionUnavailableError()
    # The key is scoped to principal and workspace, not the destination: reuse
    # for a different thread is an explicit conflict rather than a new effect.
    identity = json.dumps([workspace_id, user_id, delivery_id], separators=(",", ":"))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    receipt_id = f"o.HarnessProactiveDeliveryRecord.{digest}"
    message_id = f"n.ChatMessage.{digest}"
    # Participating workspace/identity revocations serialize with this short
    # write on transactional stores. The notification is emitted after commit.
    async with release_admission(user_id, workspace_id):
        thread = await find_thread_by_provider_session(
            user_id=user_id,
            workspace_id=workspace_id,
            provider_session_id=session_id,
        )
        if thread is None:
            return None
        if graph_transaction_available():
            transaction = current_graph_transaction_database()
            row = await transaction.lock_record("node", thread.id)
            if row is None or row.get("entity") != ChatThread._entity_name():
                return None
            thread = await get_default_context()._deserialize_entity(ChatThread, row)
            if thread is None or not _matches_binding(
                thread, user_id, workspace_id, session_id
            ):
                return None
        parts: List[Dict[str, Any]] = [{"type": "text", "text": content}]
        public_metadata = {**(metadata or {}), "origin": "proactive"}
        intent = json.dumps(
            {
                "thread_id": thread.id,
                "session_id": session_id,
                "parts": parts,
                "provider_metadata": public_metadata,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        intent_digest = hashlib.sha256(intent.encode("utf-8")).hexdigest()
        scope_key = thread_scope_key(
            principal_id=user_id, workspace_id=workspace_id, thread_id=thread.id
        )
        receipt, created = await HarnessProactiveDeliveryRecord.create_if_absent(
            id=receipt_id,
            scope_key=scope_key,
            principal_id=user_id,
            workspace_id=workspace_id,
            thread_id=thread.id,
            message_id=message_id,
            intent_digest=intent_digest,
            created_at=utc_now_iso(),
        )
        receipt_row = await transaction.lock_record("object", receipt.id)
        fact = (receipt_row or {}).get("context") or {}
        if (
            not receipt_row
            or receipt_row.get("entity")
            != HarnessProactiveDeliveryRecord._entity_name()
            or fact.get("scope_key") != scope_key
            or fact.get("principal_id") != user_id
            or fact.get("workspace_id") != workspace_id
            or fact.get("thread_id") != thread.id
            or fact.get("message_id") != message_id
            or fact.get("intent_digest") != intent_digest
        ):
            raise ResourceConflictError(
                message="Proactive delivery ID is bound to different content or destination"
            )
        if created:
            message = await chat_store.append_message(
                thread=thread,
                role="assistant",
                parts=parts,
                provider_metadata=public_metadata,
                message_id=message_id,
            )
        else:
            message = await ChatMessage.get(message_id)
            edges = await get_default_context().find_edges_between(
                thread.id, message_id, edge_class=CONTAINS
            )
            if (
                message is None
                or message.thread_id != thread.id
                or message.role != "assistant"
                or message.parts != parts
                or message.provider_metadata != public_metadata
                or not edges
            ):
                raise ResourceConflictError(
                    message="Proactive delivery receipt cannot be reconciled"
                )
    await notify_thread_message(
        user_id,
        thread.id,
        message_id=message.id,
        role="assistant",
        origin="proactive",
    )
    return thread.id
