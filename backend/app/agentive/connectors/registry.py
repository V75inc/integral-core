"""Connector dispatch registry — vendor-keyed lookup by AgentConfig.agent_type.

Per AGT-04 + D-11: connector authors register via @register_connector("<kind>") decorator.
No edit to this file is required to add a new connector kind — only the new module
file with the decorator import. Side-effect imports below ensure built-in connectors
register at module-load time.

Aliases (e.g., "integral_assistant", "") are NOT spread into the registry — they
are normalized at the get_chat_connector lookup boundary. This keeps the AgentType
Literal as the single canonical key set (D-09 / RESEARCH Pitfall 3).
"""

from __future__ import annotations

from typing import Callable, Dict, Type

from app.agentive.connectors.base import AgentChatConnector

_REGISTRY: Dict[str, Type[AgentChatConnector]] = {}


def register_connector(
    agent_type: str,
) -> Callable[[Type[AgentChatConnector]], Type[AgentChatConnector]]:
    """Class decorator. Registers the class as the connector for `agent_type`.

    Aliases (e.g., "integral_assistant", "") are NOT spread into the registry —
    they are normalized at the get_chat_connector lookup boundary. This keeps the
    AgentType Literal as the single canonical key set (D-09).

    Raises ValueError if agent_type is empty or already registered.
    """

    def _decorator(cls: Type[AgentChatConnector]) -> Type[AgentChatConnector]:
        key = (agent_type or "").strip().lower()
        if not key:
            raise ValueError("register_connector requires a non-empty agent_type")
        if key in _REGISTRY:
            raise ValueError(
                f"agent_type {key!r} already registered to {_REGISTRY[key].__name__}"
            )
        _REGISTRY[key] = cls
        return cls

    return _decorator


def get_chat_connector(agent_type: str) -> AgentChatConnector:
    """Resolve connector by agent_type. Aliases normalize here, not in _REGISTRY."""
    t = (agent_type or "").strip().lower()
    # Alias-normalization boundary — preserved from prior shape (RESEARCH Pitfall 3).
    if t in ("integral_assistant", ""):
        t = "jvagent"
    cls = _REGISTRY.get(t)
    if cls is None:
        raise ValueError(f"Unsupported agent_type for chat: {agent_type!r}")
    return cls()


# MCP stub connector is quarantine-gated (Full Sweep B3): default OFF in prod.
# Opt in with INTEGRAL_ENABLE_MCP_STUB_CONNECTOR=1, or when TESTING / pytest.
import os as _os  # noqa: E402

# Side-effect imports — register built-in connectors at module load time.
# These imports MUST stay at the bottom (avoid circular imports through @register_connector).
from app.agentive.connectors import jvagent_connector  # noqa: E402, F401

if (
    _os.getenv("INTEGRAL_ENABLE_MCP_STUB_CONNECTOR") == "1"
    or _os.getenv("TESTING")
    or _os.getenv("PYTEST_CURRENT_TEST")
):
    from app.agentive.connectors import mcp_stub_connector  # noqa: E402, F401
