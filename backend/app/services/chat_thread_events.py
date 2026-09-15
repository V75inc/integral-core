"""Chat thread activity notifications over the agent-events WebSocket."""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.agentive.services.agent_events import push_agent_event


async def notify_thread_stream_update(
    user_id: str,
    thread_id: str,
    *,
    status: str,
    workspace_id: Optional[str] = None,
    turn_id: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Notify connected clients that a thread's stream state changed.

    ``workspace_id`` and ``turn_id`` exist so a receiving tab can act on the
    event rather than only react to it. Events fan out to every socket the
    user has open, regardless of which workspace that tab is showing — so
    without the workspace a tab cannot tell whether a running thread belongs
    to what is on screen. And a fast completed-then-started pair is
    indistinguishable without a turn id, which makes "is this thread busy?"
    unanswerable at exactly the moment it matters.
    """
    payload: Dict[str, Any] = {
        "thread_id": thread_id,
        "status": status,
    }
    if workspace_id:
        payload["workspace_id"] = workspace_id
    if turn_id:
        payload["turn_id"] = turn_id
    if extra:
        payload.update(extra)
    await push_agent_event(user_id, "thread_stream_update", payload)


async def notify_thread_message(
    user_id: str,
    thread_id: str,
    *,
    message_id: str,
    role: str,
    origin: str = "proactive",
) -> None:
    """Notify clients that a new persisted message landed on a thread."""
    await push_agent_event(
        user_id,
        "thread_stream_update",
        {
            "thread_id": thread_id,
            "status": "message",
            "message_id": message_id,
            "role": role,
            "origin": origin,
        },
    )
