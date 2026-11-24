"""Chat-agent dispatch. Not the connector package registry.

External agent runtimes register with ``@register_chat_agent``. Connector
packages (MCP, sync, actions, channels) use the connector catalogue and
managed-channel registry instead.
"""

from __future__ import annotations

from typing import Callable, Dict, Type

from app.agentive.connectors.base import AgentChatConnector

_REGISTRY: Dict[str, Type[AgentChatConnector]] = {}


def register_chat_agent(
    agent_type: str,
) -> Callable[[Type[AgentChatConnector]], Type[AgentChatConnector]]:
    """Register one external agent runtime for ``agent_type``.

    Raises ValueError if agent_type is empty or already registered.
    """

    def _decorator(cls: Type[AgentChatConnector]) -> Type[AgentChatConnector]:
        key = (agent_type or "").strip().lower()
        if not key:
            raise ValueError("register_chat_agent requires a non-empty agent_type")
        if key in _REGISTRY:
            raise ValueError(
                f"agent_type {key!r} already registered to {_REGISTRY[key].__name__}"
            )
        _REGISTRY[key] = cls
        return cls

    return _decorator


def get_chat_agent(agent_type: str) -> AgentChatConnector:
    """Resolve an external agent runtime by agent_type."""
    t = (agent_type or "").strip().lower()
    # Alias-normalization boundary — preserved from prior shape (RESEARCH Pitfall 3).
    cls = _REGISTRY.get(t)
    if cls is None:
        raise ValueError(f"Unsupported agent_type for chat: {agent_type!r}")
    return cls()


# MCP stub connector is quarantine-gated (Full Sweep B3): default OFF in prod.
# Opt in with INTEGRAL_ENABLE_MCP_STUB_CONNECTOR=1, or when TESTING / pytest.
import os as _os  # noqa: E402

# Side-effect imports — register built-in connectors at module load time.
# These imports MUST stay at the bottom (avoid circular imports through @register_connector).

if (
    _os.getenv("INTEGRAL_ENABLE_MCP_STUB_CONNECTOR") == "1"
    or _os.getenv("TESTING")
    or _os.getenv("PYTEST_CURRENT_TEST")
):
    from app.agentive.connectors import mcp_stub_connector  # noqa: E402, F401
