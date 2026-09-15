"""WebSocket endpoint for real-time ChangeEvent subscriptions (EVT-01).

Per CONTEXT D-13: JWT-via-query-param auth (mirrors Phase 1 agent_events pattern
verbatim). Core endpoint — NOT gated on AGENTIVE_ENABLED. Subscribers receive
ChangeEvents that pass the D-07 per-message permission filter applied inside
`event_subscription_registry.broadcast_change_event` before each ws.send_text.

Path: WS /api/events
Auth: ?token=<JWT>  (HS256, signed with settings.SECRET_KEY)

Optional query params (filter narrowing):
  - scope=track:<id> | app:<id> | user:<id>
  - actor_kind=human|agent|connector|system

Phase 2 supports filter narrowing via permission scope (the WS only receives
events the subscriber's permission allows). Plan 02-04 polling endpoint exposes
fine-grained scope/actor_kind query parameters for explicit narrowing; for the
WS, the D-07 broadcast filter in event_subscription_registry already enforces
visibility.

WebSocket note: jvspatial's ``@endpoint`` decorator does not currently support
WebSocket routes (it dispatches via the HTTP endpoint router only). This file
therefore retains the FastAPI ``APIRouter().websocket(...)`` primitive with
inline ``# deviation:`` annotation per CLAUDE.md § Forbidden Patterns →
Pragmatism Clause. The router is mounted into the FastAPI app via main.py's
``app.include_router(events_ws_router)`` (see events_ws_router export below).
"""

import json
import logging

from fastapi import (  # deviation: @endpoint does not support WebSocket — fastapi APIRouter required for ws routes (jvspatial framework limitation)
    APIRouter,
    WebSocket,
    WebSocketDisconnect,
)

from app.services.change_event_logger import is_change_event_enabled
from app.services.event_subscription_registry import (
    register_subscription,
    unregister_subscription,
)
from app.services.ws_auth import resolve_websocket_user_id

logger = logging.getLogger(__name__)

router = APIRouter()

EVENTS_WS_PATH = "/api/events"


@router.websocket(EVENTS_WS_PATH)
async def events_websocket(websocket: WebSocket) -> None:
    """WS subscription endpoint for ChangeEvents.

    Per CONTEXT D-13: JWT-via-query-param auth pattern (mirrors agent_events).
    Per CONTEXT D-07: per-message permission re-check applied in
    event_subscription_registry.broadcast_change_event before each send.
    """
    await websocket.accept()

    # Kill switch: ChangeEvents disabled — no broadcasts will ever fire.
    # Close immediately with a policy-violation code so clients fail fast
    # instead of waiting on a silent connection.
    if not is_change_event_enabled():
        await websocket.close(
            code=1008,
            reason="ChangeEvent emission disabled (CHANGE_EVENT_ENABLED=False)",
        )
        return

    # Authenticate via short-lived ticket (preferred) or legacy JWT query param.
    user_id = await resolve_websocket_user_id(websocket)

    if not user_id:
        await websocket.close(code=4001, reason="Authentication required")
        return

    await register_subscription(user_id, websocket)
    logger.info("Events WebSocket connected for user %s", user_id)

    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
            except (json.JSONDecodeError, KeyError):
                pass
    except WebSocketDisconnect:
        logger.info("Events WebSocket disconnected for user %s", user_id)
    except Exception as e:
        logger.warning("Events WebSocket error for user %s: %s", user_id, e)
    finally:
        await unregister_subscription(user_id, websocket)
