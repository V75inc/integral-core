"""jvagent interact API → Integral chat shape."""

from __future__ import annotations

import logging
from typing import Any, Dict

import httpx

from app.agentive.connectors.base import (
    AgentChatConnector,
    ChatTurnContext,
    ChatTurnResult,
)
from app.agentive.connectors.registry import register_connector
from app.config import settings

logger = logging.getLogger(__name__)

INTEGRAL_CHANNEL = "integral"


@register_connector("jvagent")
class JvAgentConnector(AgentChatConnector):
    async def send_turn(
        self,
        ctx: ChatTurnContext,
        *,
        preferences: Dict[str, Any],
    ) -> ChatTurnResult:
        """Forward a chat turn to the configured JVAgent and return the result."""
        base = (
            (preferences.get("jvagent_base_url") or "").strip()
            or (settings.JVAGENT_BASE_URL or "").strip()
        ).rstrip("/")
        agent_id = (preferences.get("jvagent_agent_id") or "").strip() or (
            settings.INTEGRAL_JVAGENT_AGENT_ID or ""
        ).strip()
        if not base or not agent_id:
            return ChatTurnResult(
                error=(
                    "jvagent is not configured: set JVAGENT_BASE_URL and "
                    "INTEGRAL_JVAGENT_AGENT_ID (or register-system preferences)"
                )
            )

        url = f"{base}/api/agents/{agent_id}/interact"
        payload: Dict[str, Any] = {
            "utterance": ctx.message,
            "channel": INTEGRAL_CHANNEL,
            "user_id": ctx.email,
            "data": {
                "focused_track_id": ctx.focused_track_id,
                "focused_space_id": ctx.focused_space_id,
                "workspace_id": ctx.extra.get("workspace_id"),
                **ctx.extra,
            },
        }
        if ctx.session_id:
            payload["session_id"] = ctx.session_id

        try:
            # One POST spans the whole turn; see
            # INTEGRAL_AGENT_TURN_TIMEOUT_SECONDS for why this is minutes, not
            # seconds. Connect stays short so a genuinely unreachable service
            # still fails fast instead of hanging the chat for 15 minutes.
            timeout = httpx.Timeout(
                settings.INTEGRAL_AGENT_TURN_TIMEOUT_SECONDS,
                connect=10.0,
            )
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, json=payload)
        except httpx.RequestError as e:
            logger.warning("jvagent interact request failed: %s", e)
            return ChatTurnResult(error="Could not reach the agent service.")

        try:
            data = response.json()
        except Exception:
            data = {"_text": response.text}

        if response.status_code >= 400:
            err = data.get("message") or data.get("detail") or response.text
            logger.warning(
                "jvagent interact HTTP %s: %s",
                response.status_code,
                err,
            )
            return ChatTurnResult(
                error=str(err) if err else f"Agent error ({response.status_code})",
                raw=data if isinstance(data, dict) else None,
            )

        if not isinstance(data, dict):
            return ChatTurnResult(error="Invalid agent response", raw=None)

        text = data.get("response") or data.get("message") or ""
        sid = data.get("session_id") or ""
        uid = data.get("user_id") or ctx.email
        return ChatTurnResult(
            message=str(text) if text else "",
            session_id=str(sid) if sid else "",
            agent_user_id=str(uid) if uid else "",
            raw=data,
        )
