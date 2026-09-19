"""Minimal change-feed → routine/agent-turn wake hooks (Full Sweep R4).

Durable workspace event streams remain a roadmap item. This module offers a
best-effort in-process notifier so routines and proactive digests can react
to substrate writes without waiting on the clock alone.

Callers (change_event emitters) may invoke :func:`notify_substrate_change`
after a successful write. Subscribers register via :func:`register_wake_handler`.
Handlers must be cheap and fail-closed — exceptions are logged, never raised.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

WakeHandler = Callable[[Dict[str, Any]], Awaitable[None]]

_handlers: List[WakeHandler] = []


def register_wake_handler(handler: WakeHandler) -> None:
    """Register an async handler for substrate change wake-ups."""
    if handler not in _handlers:
        _handlers.append(handler)


def clear_wake_handlers() -> None:
    """Test helper — drop all handlers."""
    _handlers.clear()


async def notify_substrate_change(
    *,
    action: str,
    resource_type: str,
    resource_id: str,
    actor_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Fan out a change wake-up to registered handlers (best-effort).

    Latency hint only — durable event → work enqueue is owned by
    ``app.agentive.services.work_events`` and its checkpoint consumer.
    """
    event: Dict[str, Any] = {
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "actor_id": actor_id,
        "workspace_id": workspace_id,
        **(extra or {}),
    }
    for handler in list(_handlers):
        try:
            await handler(event)
        except Exception:
            logger.exception(
                "event_wake: handler %s failed for action=%s resource=%s:%s",
                getattr(handler, "__name__", handler),
                action,
                resource_type,
                resource_id,
            )
