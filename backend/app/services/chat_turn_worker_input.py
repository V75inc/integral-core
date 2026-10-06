"""Rebuild trusted native chat input from a claimed WorkItem and capsule."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.agentive.harness.turn_input import load_turn_input_capsule
from app.agentive.work_models import WorkItem
from app.models.edges import CONTAINS
from app.models.nodes import ChatMessage, ChatThread, User
from app.schemas.agentive.work import ChatTurnExecutionContext, WorkError


@dataclass(frozen=True)
class ChatTurnWorkerInput:
    """Server-rebuilt input; content fields are excluded from repr/logging."""

    thread: ChatThread = field(repr=False)
    message: ChatMessage = field(repr=False)
    user_email: str = field(repr=False)
    execution_context: ChatTurnExecutionContext = field(repr=False)
    text: str = field(repr=False)


async def load_claimed_chat_turn_input(item: WorkItem) -> ChatTurnWorkerInput:
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
    user = await User.get(principal_id)
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
        or message.role != "user"
        or set(message.provider_metadata or {}).difference(
            {"entity_refs", "page_context"}
        )
        or user is None
        or user.id != principal_id
    ):
        raise WorkError("work.policy_denied", "chat turn graph scope mismatch")

    # V1 worker admission deliberately supports text-only turns. Attachment
    # bytes and image URLs must not be reconstructed from an opaque browser
    # payload; they need a separate authorized artifact contract first.
    parts = list(message.parts or [])
    if (
        len(parts) != 1
        or not isinstance(parts[0], dict)
        or set(parts[0]) != {"type", "text"}
        or parts[0].get("type") != "text"
        or not isinstance(parts[0].get("text"), str)
        or not parts[0]["text"].strip()
    ):
        raise WorkError(
            "work.policy_denied",
            "native durable turns currently require text-only input",
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
        text=sanitize_user_text(parts[0]["text"]),
    )


__all__ = ["ChatTurnWorkerInput", "load_claimed_chat_turn_input"]
