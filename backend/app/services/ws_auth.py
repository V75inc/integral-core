"""Shared WebSocket authentication helpers."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import WebSocket

from app.services.ws_ticket import redeem_ws_ticket

logger = logging.getLogger(__name__)


async def resolve_websocket_user_id(websocket: WebSocket) -> Optional[str]:
    """Resolve user id from ``?ticket=`` (preferred) or legacy ``?token=`` JWT."""
    ticket = websocket.query_params.get("ticket", "")
    if ticket:
        user_id = redeem_ws_ticket(ticket)
        if user_id:
            return user_id
        logger.warning("WebSocket ticket auth failed: invalid or expired ticket")
        return None

    token = websocket.query_params.get("token", "")
    if not token:
        return None

    try:
        from app.api.auth import _get_auth_service

        auth_service = _get_auth_service()
        user_response = await auth_service.validate_token(token)
        if user_response is not None:
            return getattr(user_response, "user_id", None) or getattr(
                user_response, "id", None
            )
    except Exception as exc:
        logger.warning("WebSocket token auth failed: %s", exc)
    return None
