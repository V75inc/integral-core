"""Stub MCP connector — proves the dispatch table is vendor-neutral (D-11).

Full Sweep B3: registration is quarantined behind
``INTEGRAL_ENABLE_MCP_STUB_CONNECTOR=1`` or TESTING. Default off so production
never routes ``agent_type=mcp`` to this echo stub.

Returns a deterministic ChatTurnResult without making any network call.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict

from app.agentive.connectors.base import (
    AgentChatConnector,
    ChatTurnContext,
    ChatTurnResult,
)
from app.agentive.connectors.registry import register_connector

logger = logging.getLogger(__name__)


def _stub_enabled() -> bool:
    if os.getenv("TESTING", "").strip().lower() in ("1", "true", "yes"):
        return True
    return os.getenv("INTEGRAL_ENABLE_MCP_STUB_CONNECTOR", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


class McpStubConnector(AgentChatConnector):
    """Vendor-neutral dispatch proof (quarantined — Full Sweep B3)."""

    async def send_turn(
        self,
        ctx: ChatTurnContext,
        *,
        preferences: Dict[str, Any],
    ) -> ChatTurnResult:
        """Return a stub echo response without making any network call."""
        return ChatTurnResult(
            message=f"[mcp-stub] received: {ctx.message}",
            session_id=ctx.session_id or "stub-session",
            agent_user_id=ctx.email,
            raw={
                "stub": True,
                "endpoint": (preferences.get("mcp_endpoint") or ""),
            },
        )


if _stub_enabled():
    register_connector("mcp")(McpStubConnector)
    logger.info("mcp_stub_connector: registered (stub enabled)")
else:
    logger.debug(
        "mcp_stub_connector: not registered "
        "(set INTEGRAL_ENABLE_MCP_STUB_CONNECTOR=1 to enable)"
    )
