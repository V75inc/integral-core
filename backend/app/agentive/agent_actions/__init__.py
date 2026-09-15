"""jvagent agent-actions registry — harness-tier (AGENTIVE_ENABLED-only).

Per CONTEXT lock #11: agent actions live inside the AGENTIVE_ENABLED
conditional load path (``backend/app/agentive/``). The MCP-side override
map (``MCP_TOOL_NAME_OVERRIDES`` in ``backend/app/agentive/tooling/name_overrides.py``)
lives in the always-loaded core service path — that surface is what an
external MCP client (Claude Desktop, Cursor) consumes. THIS surface is
what the in-process jvagent tool loop consumes.

Actions are async callables invokable from inside the agent tool loop.
Phase 4 lands the first action: ``retrieve_context`` (RET-04).

Pattern (used by Phase 5+ agent-action additions):

  1. Define an async callable in ``agent_actions/<name>.py`` that takes
     ``agent_id`` + action-specific kwargs and returns a typed response.
  2. Eager-import the callable here.
  3. Register it via ``register_action(name, fn)``.

The registry is a single module-level dict. ``get_registered_actions()``
returns a snapshot for the agent loop's tool-dispatch table.
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
