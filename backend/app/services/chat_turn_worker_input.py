"""Rebuild trusted native chat input from a claimed WorkItem and capsule."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.agentive.harness.turn_input import load_turn_input_capsule
from app.agentive.work_models import WorkItem
from app.models.edges import CONTAINS
from app.models.nodes import ChatMessage, ChatThread
from app.schemas.agentive.work import (
    ChatTurnExecutionContext,
    ChatTurnSubmissionRequest,
    WorkError,
)
from app.services.chat_turn_submissions import assert_accepted_chat_turn_fingerprint


@dataclass(frozen=True)
class ChatTurnWorkerInput:
    """Server-rebuilt input; content fields are excluded from repr/logging."""

    thread: ChatThread = field(repr=False)
    message: ChatMessage = field(repr=False)
    user_email: str = field(repr=False)
    execution_context: ChatTurnExecutionContext = field(repr=False)
    text: str = field(repr=False)


async def load_claimed_chat_turn_input(
    item: WorkItem, *, recheck_host_control: bool = True
) -> ChatTurnWorkerInput:
    """Authenticate the exact WorkItem/capsule/message tuple before execution.

    All identifiers come from the claimed row and its encrypted capsule. The
    worker never takes tenant, principal, message, or context identity from a
    browser reconnect or model-generated tool call.
    """
    if item.kind != "chat_turn" or item.status != "running":
        raise WorkError("work.policy_denied", "chat turn is not actively claimed")
    principal_id = (item.principal_id or "").strip()
    workspace_id = (item.workspace_id or "").strip()
    thread_id = (item.thread_id or "").strip()
    work_item_id = (item.work_item_id or "").strip()
    payload = dict(item.input_payload or {})
    capsule_id = str(payload.get("capsule_id") or "").strip()
    capsule_digest = str(payload.get("capsule_digest") or "").strip()
    accepted_message_id = str(payload.get("accepted_message_id") or "").strip()
    if not all(
        (
            principal_id,
            workspace_id,
            thread_id,
            work_item_id,
            capsule_id,
            capsule_digest,
            accepted_message_id,
        )
    ):
        raise WorkError("work.policy_denied", "chat turn input reference is incomplete")

    try:
        capsule = await load_turn_input_capsule(
            capsule_id=capsule_id,
            expected_digest=capsule_digest,
            principal_id=principal_id,
            workspace_id=workspace_id,
            thread_id=thread_id,
            work_item_id=work_item_id,
        )
    except (LookupError, ValueError) as exc:
        raise WorkError("work.policy_denied", "chat turn input is unavailable") from exc

    if (
        capsule.accepted_message_id != accepted_message_id
        or capsule.principal_id != principal_id
        or capsule.workspace_id != workspace_id
        or capsule.thread_id != thread_id
        or capsule.work_item_id != work_item_id
    ):
        raise WorkError("work.policy_denied", "chat turn input scope mismatch")

    thread = await ChatThread.get(thread_id)
    message = await ChatMessage.get(accepted_message_id)
    from app.services.permissions import get_user_node

    # HTTP principals use AuthUser IDs; resolve their linked graph User using
    # the same identity contract as workspace authorization.
    user = await get_user_node(principal_id)
    if (
        thread is None
        or thread.id != thread_id
        or thread.provider_id != "integral_native"
        or thread.user_id != principal_id
        or thread.workspace_id != workspace_id
        or thread.active_work_item_id != work_item_id
        or message is None
        or message.id != accepted_message_id
        or message.thread_id != thread_id
        or message.role
        != ("system" if capsule.execution_context.host_control else "user")
        or set(message.provider_metadata or {}).difference(
            {"entity_refs", "page_context"}
        )
        or user is None
        or principal_id not in {user.id, user.user_id}
    ):
        raise WorkError("work.policy_denied", "chat turn graph scope mismatch")

    try:
        restored_request = ChatTurnSubmissionRequest(
            principal_id=principal_id,
            workspace_id=workspace_id,
            thread_id=thread_id,
            client_request_id=capsule.client_request_id,
            parts=list(message.parts or []),
            provider_metadata=dict(message.provider_metadata or {}),
            parent_id=message.parent_id,
            execution_context=capsule.execution_context,
        )
    except ValueError as exc:
        raise WorkError("work.policy_denied", "accepted chat input is invalid") from exc
    assert_accepted_chat_turn_fingerprint(
        request=restored_request,
        expected_fingerprint=str(payload.get("request_fingerprint") or ""),
    )

    parts = list(message.parts or [])
    text_parts = []
    for part in parts:
        if not isinstance(part, dict):
            raise WorkError("work.policy_denied", "accepted chat part is invalid")
        if (
            part.get("type") == "text"
            and set(part) == {"type", "text"}
            and isinstance(part.get("text"), str)
        ):
            text_parts.append(part["text"])
        elif part.get("type") != "file":
            raise WorkError("work.policy_denied", "accepted chat part is unsupported")
    if len(text_parts) > 1 or (
        not any(text.strip() for text in text_parts)
        and not any(part.get("type") == "file" for part in parts)
    ):
        raise WorkError("work.policy_denied", "accepted chat input is empty")

    from app.services.chat_turn_attachments import assert_chat_attachment_bindings

    await assert_chat_attachment_bindings(
        thread=thread,
        principal_id=principal_id,
        parts=parts,
        expected=capsule.execution_context.attachment_bindings,
    )

    from app.services.chat_turn_host_controls import assert_chat_host_control

    if recheck_host_control:
        await assert_chat_host_control(
            thread=thread,
            principal_id=principal_id,
            expected=capsule.execution_context.host_control,
        )

    from app.services.workspace_permissions import can_access_workspace

    if await can_access_workspace(principal_id, workspace_id) == "none":
        raise WorkError("work.policy_denied", "workspace access was revoked")

    graph = await thread.get_context()
    if not await graph.find_edges_between(
        thread_id, accepted_message_id, edge_class=CONTAINS
    ):
        raise WorkError("work.policy_denied", "accepted chat message is detached")

    from app.services.chat_page_context import sanitize_user_text

    return ChatTurnWorkerInput(
        thread=thread,
        message=message,
        # The native provider keys authority by principal_id. Email is a
        # legacy adapter field and is neither stored in WorkItem nor required
        # to resume a native turn.
        user_email="",
        execution_context=capsule.execution_context,
        # Keep the transcript faithful to the user's authored text, but apply
        # the same host-marker neutralization as the live /messages path
        # before the model sees it. Durable recovery must not let a user forge
        # a host-generated [SYSTEM:...] directive by sending raw transcript
        # content straight to the provider.
        text=(
            ""
            if capsule.execution_context.host_control
            else sanitize_user_text("\n".join(text_parts))
        ),
    )


__all__ = ["ChatTurnWorkerInput", "load_claimed_chat_turn_input"]
