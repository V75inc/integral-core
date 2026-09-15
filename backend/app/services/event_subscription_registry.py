"""Core event subscription registry — connections + broadcast helper for /api/events WS.

Per CONTEXT D-12: this module lives in core (``app/services/``), NOT agentive. The
/api/events WS endpoint is core infrastructure available without AGENTIVE_ENABLED.
The Phase 1 sibling at ``app/agentive/services/agent_events.py`` is preserved
verbatim (W3 invariant).

Per CONTEXT D-07: every broadcast call passes ``can_view_track`` /
``can_view_space`` / identity check before send. No caching of permission
decisions — revocation takes effect on the very next broadcast.

Per CONTEXT D-13: JWT-via-query-param auth (NOT service-auth) — see
``app/api/events_ws.py`` for the authentication path.

Per CONTEXT D-06: broadcast is best-effort. Failure does NOT roll back the
persisted DBLog row. Subscribers who miss a broadcast can recover via EVT-02
polling on ``since=<cursor>``.

Wire shape: ``ChangeEventEnvelope`` (see ``app/services/change_event_logger.py``)
is the runtime ChangeEvent type. The envelope is produced by ``emit_change_event``
and passed to this registry; WS subscribers and consumer hooks both receive it.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable, Dict, List

from fastapi import WebSocket

from app.schemas.policy import Resource, Subject
from app.services.change_event_logger import (
    ChangeEventEnvelope,
    is_change_event_enabled,
)
from app.services.policy_engine import evaluate as policy_evaluate
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)

# user_id -> list of active WebSocket connections.
_subscriptions: Dict[str, List[WebSocket]] = {}

# In-process consumer hooks. Bypass the per-message D-07 permission filter —
# they are server-internal code, not subscribers.
_consumer_hooks: List[Callable[[ChangeEventEnvelope], Awaitable[None]]] = []


def register_consumer_hook(
    callback: Callable[[ChangeEventEnvelope], Awaitable[None]],
) -> None:
    """Register an in-process consumer to receive every ChangeEvent post-emit.

    Hooks bypass the per-message permission filter (D-07) — they are
    server-internal code, not subscribers. The consumer is responsible for any
    access checks it performs.
    """
    _consumer_hooks.append(callback)


def reset_consumer_hooks() -> None:
    """Test helper — clear all registered consumer hooks. Not for production."""
    _consumer_hooks.clear()


async def register_subscription(user_id: str, websocket: WebSocket) -> None:
    """Register a new subscriber connection for the given user."""
    _subscriptions.setdefault(user_id, []).append(websocket)
    logger.info(
        "event subscription registered: user=%s active=%d",
        user_id,
        len(_subscriptions[user_id]),
    )


async def unregister_subscription(user_id: str, websocket: WebSocket) -> None:
    """Remove a subscriber connection."""
    if user_id in _subscriptions and websocket in _subscriptions[user_id]:
        _subscriptions[user_id].remove(websocket)
        if not _subscriptions[user_id]:
            del _subscriptions[user_id]


async def _user_permitted_for_scope(user_id: str, scope: str) -> bool:
    """D-07 per-message permission check via policy_engine.evaluate.

    The engine's default-human branch dispatches on resource.scope prefix
    (track:/space:/user:) — verbatim replacement of the legacy decision tree.
    No caching at this layer — revocation takes effect on the very next
    broadcast (the engine's per-request cache contextvar is empty in the
    WS broadcast path because there is no inbound HTTP request).
    """
    decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="event_feed.subscribe",
        resource=Resource(
            kind="change_event",
            id="",
            scope=scope,
        ),
    )
    return decision.allowed


async def broadcast_change_event(event: ChangeEventEnvelope) -> None:
    """Push a ChangeEvent to all permitted subscribers + in-process consumer hooks.

    Per CONTEXT D-06: broadcast is best-effort. Failure does NOT roll back the
    persisted DBLog row. Subscribers who miss a broadcast can recover via
    EVT-02 polling on ``since=<cursor>``.

    Dispatch order:
    1. WS subscribers — filtered through D-07 permission check per user
    2. In-process consumer hooks — no permission filter, server-internal

    A failure in either branch is logged and swallowed.
    """
    if not is_change_event_enabled():
        return

    if _subscriptions:
        scope = event.scope or ""
        payload: Dict[str, Any] = event.to_wire()
        message = json.dumps(
            {
                "type": "change_event",
                "payload": payload,
                "timestamp": utc_now_iso(),
            }
        )

        for user_id, websockets in list(_subscriptions.items()):
            permitted = await _user_permitted_for_scope(user_id, scope)
            if not permitted:
                continue

            dead: List[WebSocket] = []
            for ws in websockets:
                try:
                    await ws.send_text(message)
                except Exception as e:
                    logger.warning(
                        "event broadcast: dead connection user=%s err=%s", user_id, e
                    )
                    dead.append(ws)
            for ws in dead:
                await unregister_subscription(user_id, ws)

    for hook in list(_consumer_hooks):
        try:
            await hook(event)
        except Exception as e:
            logger.warning("event broadcast: consumer hook failed: %s", e)
