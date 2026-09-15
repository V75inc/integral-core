"""WebSocket event stream management for the agentive layer.

Manages active WebSocket connections and broadcasts graph change
events to connected agent/WebSocket clients.
"""

import json
import logging
from typing import Any, Dict

from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)

# Active WebSocket connections: user_id -> [websocket, ...]
_agent_event_connections: Dict[str, list] = {}


async def register_agent_event_connection(user_id: str, websocket) -> None:
    """Register a WebSocket connection for agent event streaming."""
    if user_id not in _agent_event_connections:
        _agent_event_connections[user_id] = []
    _agent_event_connections[user_id].append(websocket)


async def unregister_agent_event_connection(user_id: str, websocket) -> None:
    """Unregister a WebSocket connection."""
    if user_id in _agent_event_connections:
        _agent_event_connections[user_id] = [
            ws for ws in _agent_event_connections[user_id] if ws is not websocket
        ]
        if not _agent_event_connections[user_id]:
            del _agent_event_connections[user_id]


async def push_agent_event(
    user_id: str, event_type: str, payload: Dict[str, Any]
) -> None:
    """Push a graph change event to all connected agent/WebSocket clients for a user."""
    if user_id not in _agent_event_connections:
        return

    message = json.dumps(
        {
            "type": event_type,
            "payload": payload,
            "timestamp": utc_now_iso(),
        }
    )

    dead = []
    for ws in _agent_event_connections[user_id]:
        try:
            await ws.send_text(message)
        except Exception:
            dead.append(ws)

    for ws in dead:
        await unregister_agent_event_connection(user_id, ws)
