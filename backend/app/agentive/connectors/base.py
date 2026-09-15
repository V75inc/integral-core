"""Abstract agent chat connector — vendor-agnostic turn in/out."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class ChatTurnContext:
    """One user message from Integral (already authenticated)."""

    email: str
    message: str
    session_id: Optional[str] = None
    focused_track_id: Optional[str] = None
    focused_space_id: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatTurnResult:
    """Normalized reply for the Integral web UI."""

    message: str = ""
    session_id: str = ""
    agent_user_id: str = ""
    error: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None


class AgentChatConnector:
    """Implement for each external agent runtime (jvagent, Claude API, …)."""

    async def send_turn(
        self,
        ctx: ChatTurnContext,
        *,
        preferences: Dict[str, Any],
    ) -> ChatTurnResult:
        """Send one chat turn to the external agent runtime and return the result."""
        raise NotImplementedError
