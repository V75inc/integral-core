"""Bridge jvagent proactive delivery to Integral ChatThread persistence."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.models.nodes import ChatThread
from app.services import chat_threads as chat_store
from app.services.chat_thread_events import notify_thread_message


async def find_thread_by_provider_session(
    *,
    user_id: str,
    provider_session_id: str,
) -> Optional[ChatThread]:
    """Return the user's ChatThread bound to a provider session id, if any."""
    if not provider_session_id:
        return None
    threads = await ChatThread.find(
        {
            "context.user_id": user_id,
            "context.provider_session_id": provider_session_id,
        }
    )
    return threads[0] if threads else None


async def persist_proactive_message_on_thread(
    *,
    user_id: str,
    session_id: str,
    content: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Persist proactive assistant text on the matching ChatThread, if any.

    Full Sweep R2: if ``session_id`` does not match a provider session, fall
    back to the user's most recently updated thread so digests still land.
    """
    thread = await find_thread_by_provider_session(
        user_id=user_id,
        provider_session_id=session_id,
    )
    if thread is None and user_id:
        # Best-effort: newest thread for this user.
        threads = await ChatThread.find({"context.user_id": user_id})
        if threads:
            threads_sorted = sorted(
                threads,
                key=lambda t: getattr(t, "updated_at", None)
                or getattr(t, "created_at", None)
                or "",
                reverse=True,
            )
            thread = threads_sorted[0]
    if thread is None:
        return None
    parts: List[Dict[str, Any]] = [{"type": "text", "text": content}]
    message = await chat_store.append_message(
        thread=thread,
        role="assistant",
        parts=parts,
        provider_metadata={
            "origin": "proactive",
            **(metadata or {}),
        },
    )
    await notify_thread_message(
        user_id,
        thread.id,
        message_id=message.id,
        role="assistant",
        origin="proactive",
    )
    return thread.id
