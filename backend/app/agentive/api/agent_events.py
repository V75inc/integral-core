"""WebSocket endpoint for real-time agent events.

Moved from main.py into the agentive layer. Only registered
when AGENTIVE_ENABLED=True.

WebSocket note: jvspatial's ``@endpoint`` decorator does not currently
support WebSocket routes (it dispatches via the HTTP endpoint router only).
This file therefore retains the FastAPI ``APIRouter().websocket(...)``
primitive with inline ``# deviation:`` annotations per AGENTS.md §
Forbidden Patterns → Pragmatism Clause. The router is wired into the
agentive surface via ``app.agentive.api`` registration. HTTP routes in
the agentive layer use ``@endpoint``; WebSocket is the documented carve-out.
"""

import json
import logging

from fastapi import (  # deviation: @endpoint does not support WebSocket — fastapi APIRouter required for ws routes (jvspatial framework limitation)
    APIRouter,
    WebSocket,
    WebSocketDisconnect,
)

from app.agentive.config import AGENTIVE_WS_PATH
from app.agentive.services.agent_events import (
    register_agent_event_connection,
    unregister_agent_event_connection,
)
from app.services.ws_auth import resolve_websocket_user_id

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket(AGENTIVE_WS_PATH)
async def agent_events_websocket(websocket: WebSocket):
    """Endpoint for real-time graph change events.

    Authenticates via JWT token in query parameter.
    Pushes graph_changed events to connected clients.
    """
    await websocket.accept()

    # Authenticate via short-lived ticket (preferred) or legacy JWT query param.
    user_id = await resolve_websocket_user_id(websocket)

    if not user_id:
        await websocket.close(code=4001, reason="Authentication required")
        return

    # Register connection
    await register_agent_event_connection(user_id, websocket)
    logger.info(f"Agent events WebSocket connected for user {user_id}")

    try:
        while True:
            # Keep alive — client may send pings
            data = await websocket.receive_text()
            # We don't expect incoming messages, but handle gracefully
            try:
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
            except (json.JSONDecodeError, KeyError):
                pass
    except WebSocketDisconnect:
        logger.info(f"Agent events WebSocket disconnected for user {user_id}")
    except Exception as e:
        logger.warning(f"Agent events WebSocket error for user {user_id}: {e}")
    finally:
        await unregister_agent_event_connection(user_id, websocket)
