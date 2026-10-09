"""Registry of in-process Core retrieval actions.

The native resident and external MCP tools use the governed tool manifest.
These callables share Core authentication, permission and retrieval services.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

_AGENT_ACTIONS_REGISTRY: Dict[str, Callable[..., Awaitable[Any]]] = {}


def register_action(name: str, fn: Callable[..., Awaitable[Any]]) -> None:
    """Register an async agent action under ``name``.

    Raises
    ------
    ValueError
        When ``name`` is already registered — duplicate registration
        usually signals a copy-paste bug in a downstream agent_actions
        module's eager-import block.
    """
    if name in _AGENT_ACTIONS_REGISTRY:
        raise ValueError(f"Agent action {name!r} already registered")
    _AGENT_ACTIONS_REGISTRY[name] = fn


def get_registered_actions() -> Dict[str, Callable[..., Awaitable[Any]]]:
    """Return a snapshot of the agent-actions registry."""
    return dict(_AGENT_ACTIONS_REGISTRY)


# ---- Eager imports — register at module load (Plan 04-03) ----
from app.agentive.agent_actions.retrieve_context import retrieve_context  # noqa: E402

register_action("retrieve_context", retrieve_context)


__all__ = [
    "register_action",
    "get_registered_actions",
    "retrieve_context",
]
