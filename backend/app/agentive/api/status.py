"""Agentive status endpoint — indicates whether the agentive layer is active."""

import logging
from typing import Any, Dict, Optional

from fastapi import Request
from jvspatial.api import endpoint

from app.agentive.api.chat import SYSTEM_CHAT_PATH
from app.agentive.services.uplink_registry import uplink_registry
from app.api.utils import resolve_principal_id
from app.config import settings
from app.services.model_credentials import get_active_credential_for_user
from app.services.request_scope import resolve_workspace_id_from_request

logger = logging.getLogger(__name__)


@endpoint("/agentive/status", methods=["GET"], auth=True, tags=["Agentive"])
async def get_agentive_status(request: Request) -> Dict[str, Any]:
    """Return agentive layer status and connected agent info."""
    status: Dict[str, Any] = {
        "enabled": True,
        "agent_connected": False,
        "agent_key_mode": (settings.INTEGRAL_AGENT_KEY_MODE or "hybrid")
        .strip()
        .lower(),
    }

    try:
        user_id = resolve_principal_id(request)

        mode = status["agent_key_mode"]
        if mode == "byo_strict" and user_id:
            workspace_id: Optional[str] = None
            try:
                workspace_id = await resolve_workspace_id_from_request(request, user_id)
            except Exception:
                workspace_id = None
            if workspace_id:
                from app.services.workspace_permissions import (
                    get_workspace_owner_user_id,
                )

                owner_node_id = await get_workspace_owner_user_id(workspace_id)
                owner_auth_id = user_id
                if owner_node_id:
                    from app.models.nodes import User

                    owner_node = await User.get(owner_node_id)
                    if owner_node and owner_node.user_id:
                        owner_auth_id = owner_node.user_id
                if not await get_active_credential_for_user(owner_auth_id):
                    status["agent_connected"] = False
                    status["reason"] = "model_key_required"
                    return status

        sys_conn = await uplink_registry.get_system_agent()
        if sys_conn and user_id:
            status["agent_connected"] = True
            status["agent_config"] = {
                "scope": sys_conn.scope,
                "agent_type": sys_conn.agent_type,
                "capabilities": sys_conn.capabilities,
                "conversation_endpoint": SYSTEM_CHAT_PATH,
            }
            return status

        if user_id:
            conn = await uplink_registry.get_agent_for_user(user_id)
            if conn:
                status["agent_connected"] = True
                status["agent_config"] = {
                    "scope": conn.scope,
                    "agent_type": conn.agent_type,
                    "capabilities": conn.capabilities,
                    "conversation_endpoint": conn.uplink_url or SYSTEM_CHAT_PATH,
                }
    except Exception as exc:
        logger.warning(
            "agentive status read failed",
            extra={"exc_type": type(exc).__name__, "exc_message": str(exc)},
        )

    return status
