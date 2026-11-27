"""External chat-agent dispatch. Trusted sync adapters load at Core startup."""

from app.agentive.connectors.base import (
    AgentChatConnector,
    ChatTurnContext,
    ChatTurnResult,
)
from app.agentive.connectors.registry import get_chat_agent

__all__ = [
    "AgentChatConnector",
    "ChatTurnContext",
    "ChatTurnResult",
    "get_chat_agent",
]
