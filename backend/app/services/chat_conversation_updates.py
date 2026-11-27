"""Committed, scoped conversation updates shared by chat interfaces.

The chat outbox remains the producer. Its relay supplies coalescible wake
hints, not data or authority. Consumers subscribe before reading a bounded
snapshot, then use the returned cursor to reconcile duplicate/delayed hints.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Literal

_MAX_TEXT_CHARS = 500
_DIGEST = re.compile(r"[a-f0-9]{64}")
_subscriptions: dict[tuple[str, str], set[ConversationSubscription]] = {}


@dataclass(eq=False)
class ConversationSubscription:
    """One coalescing wake latch, with no stored messages or event payloads."""

    _key: tuple[str, str] = field(repr=False)
    _workspace_id: str = field(repr=False)
    _dirty: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    async def wait(self) -> None:
        """Consume a wake hint; a change during a read sets the latch again."""
        await self._dirty.wait()
        self._dirty.clear()

    def close(self) -> None:
        """Unsubscribe and release a pending waiter."""
        listeners = _subscriptions.get(self._key)
        if listeners is not None:
            listeners.discard(self)
            if not listeners:
                _subscriptions.pop(self._key, None)
        self._dirty.set()


def subscribe_conversation_updates(
    principal_id: str, workspace_id: str, thread_id: str
) -> ConversationSubscription:
    """Register a content-free hint for an already admitted interface."""
    key = (principal_id, thread_id)
    listener = ConversationSubscription(key, workspace_id)
    _subscriptions.setdefault(key, set()).add(listener)
    return listener


def signal_committed_conversation_update(
    principal_id: str, thread_id: str, workspace_id: str | None
) -> None:
    """Wake matching in-process readers from the existing committed event relay."""
    for listener in _subscriptions.get((principal_id, thread_id), ()):
        # Legacy chat invalidations omit workspace. Even a matching hint
        # grants no data access; the snapshot repeats full scope admission.
        if not workspace_id or listener._workspace_id == workspace_id:
            listener._dirty.set()


@dataclass(frozen=True)
class ConversationCursor:
    """Opaque bounded transcript watermark tied to one authorized scope."""

    _scope: tuple[str, str, str, str | None] = field(repr=False)
    _window: tuple[tuple[str, str], ...] = field(repr=False)


@dataclass(frozen=True)
class ConversationUpdate:
    """One safe user-visible message projection; text is untrusted data."""

    message_id: str
    role: Literal["user", "assistant"]
    source: Literal["typed", "unknown"]
    kind: Literal["message", "attachments"]
    text: str = field(repr=False)
    attachment_count: int = 0


@dataclass(frozen=True)
class ConversationSnapshot:
    """Bounded updates and a resume watermark; a gap withholds partial history."""

    cursor: ConversationCursor
    updates: tuple[ConversationUpdate, ...] = field(repr=False)
    gap: bool = False


async def _typed_proofs(thread, messages):
    from app.agentive.work_models import WorkItem
    from app.services.chat_turn_transcript import _message_id

    accepted_ids = {
        candidate
        for message in messages
        if (candidate := message.id if message.role == "user" else message.parent_id)
    }
    if not accepted_ids:
        return set(), set()
    graph = await thread.get_context()
    rows = await graph.database.find(
        "object",
        {
            "entity": WorkItem._entity_name(),
            "context.kind": "chat_turn",
            "context.origin": "interactive_chat",
            "context.principal_id": thread.user_id,
            "context.workspace_id": thread.workspace_id,
            "context.thread_id": thread.id,
            "context.input_payload.accepted_message_id": {"$in": sorted(accepted_ids)},
            "context.input_payload.client_payload_digest": {"$exists": True},
        },
        limit=len(accepted_ids) * 2,
    )
    candidates = {}
    duplicates = set()
    for row in rows:
        context = row.get("context")
        if not isinstance(context, dict):
            continue
        payload = context.get("input_payload")
        if not isinstance(payload, dict):
            continue
        parent_id = payload.get("accepted_message_id")
        digest = payload.get("client_payload_digest")
        work_id = context.get("work_item_id")
        if (
            context.get("kind") != "chat_turn"
            or context.get("origin") != "interactive_chat"
            or context.get("principal_id") != thread.user_id
            or context.get("workspace_id") != thread.workspace_id
            or context.get("thread_id") != thread.id
            or not isinstance(parent_id, str)
            or parent_id not in accepted_ids
            or not isinstance(work_id, str)
            or not work_id
            or row.get("id") != f"o.WorkItem.{work_id}"
            or not isinstance(digest, str)
            or _DIGEST.fullmatch(digest) is None
            or payload.get("message_role", "user") != "user"
        ):
            continue
        if parent_id in candidates:
            duplicates.add(parent_id)
        candidates[parent_id] = _message_id(work_id)

    parents = set(candidates) - duplicates
    return parents, {(parent_id, candidates[parent_id]) for parent_id in parents}


def _project_message(message, typed_inputs, typed_replies):
    from app.agentive.harness.adaptive import public_dialogue

    if message.role not in ("user", "assistant"):
        return None
    typed = (
        message.id in typed_inputs
        if message.role == "user"
        else (message.parent_id, message.id) in typed_replies
    )
    dialogue = public_dialogue([message])
    text = dialogue[0].parts[0].content if dialogue else ""
    if not isinstance(text, str):
        text = ""
    text = text.strip()
    excerpt = text[:_MAX_TEXT_CHARS]
    if len(excerpt) < len(text):
        excerpt += "…"
    # Only a real accepted web input passed the durable attachment binding
    # checks. The count says a file was attached, not indexed or readable
    # now. Never release IDs, names, text extraction or raw attachment parts.
    attachment_count = 0
    if typed and message.role == "user":
        attachment_count = min(
            10,
            sum(
                1
                for part in message.parts
                if isinstance(part, dict) and part.get("type") in ("image", "file")
            ),
        )
    if not excerpt and not attachment_count:
        return None
    return ConversationUpdate(
        message_id=message.id,
        role=message.role,
        source="typed" if typed else "unknown",
        kind="attachments" if attachment_count else "message",
        text=excerpt,
        attachment_count=attachment_count,
    )


async def read_conversation_snapshot(
    principal_id: str,
    workspace_id: str,
    thread_id: str,
    *,
    profile_key: str | None = None,
    cursor: ConversationCursor | None = None,
) -> ConversationSnapshot:
    """Read current, bounded public conversation state under fresh admission.

    The cursor never authorizes access. A missing old watermark means the
    bounded window no longer proves continuity; callers can offer a deliberate
    latest-message lookup instead of automatically repeating unknown history.
    """
    from app.agentive.harness.adaptive import recent_public_messages
    from app.agentive.services.profile_management import require_native_chat_profile
    from app.api.errors import InsufficientPermissionsError
    from app.models.nodes import ChatThread
    from app.services.chat_history_authority import chat_history_workspace
    from app.services.permissions import get_user_node
    from app.services.release_admission import release_admission
    from app.services.workspace_permissions import can_access_workspace

    scope = (principal_id, workspace_id, thread_id, profile_key)
    async with release_admission(principal_id, workspace_id):
        if (
            not all(scope[:3])
            or await get_user_node(principal_id) is None
            or await can_access_workspace(principal_id, workspace_id)
            not in ("owner", "admin", "member")
            or await chat_history_workspace(principal_id, thread_id) != workspace_id
        ):
            raise InsufficientPermissionsError(message="Conversation unavailable")
        thread = await ChatThread.find_one({"id": thread_id})
        if (
            thread is None
            or thread.user_id != principal_id
            or thread.workspace_id != workspace_id
            or thread.provider_id != "integral_native"
            or thread.archived
            or (thread.provider_session_id or "").startswith("wa:")
            or (
                profile_key is not None
                and profile_key != (thread.agent_profile_key or "")
            )
        ):
            raise InsufficientPermissionsError(message="Conversation unavailable")
        if thread.agent_profile_key:
            await require_native_chat_profile(
                workspace_id, principal_id, thread.agent_profile_key
            )
        if cursor is not None and cursor._scope != scope:
            raise InsufficientPermissionsError(
                message="Conversation cursor unavailable"
            )

        messages = await recent_public_messages(thread)
        window = tuple((message.created_at or "", message.id) for message in messages)
        refreshed = ConversationCursor(scope, window)
        seen = cursor._window if cursor is not None else ()
        if seen and seen[-1] not in window:
            return ConversationSnapshot(refreshed, (), gap=True)
        known = {message_id for _, message_id in seen}
        # If an older row becomes visible only because a newer row was deleted,
        # it is not a new message. Keep same-window, out-of-order arrivals.
        unseen = [
            message
            for key, message in zip(window, messages)
            if message.id not in known and (not seen or key >= seen[0])
        ]
        if not unseen:
            return ConversationSnapshot(refreshed, ())
        typed_inputs, typed_replies = await _typed_proofs(thread, unseen)
        return ConversationSnapshot(
            refreshed,
            tuple(
                update
                for message in unseen
                if (update := _project_message(message, typed_inputs, typed_replies))
                is not None
            ),
        )
