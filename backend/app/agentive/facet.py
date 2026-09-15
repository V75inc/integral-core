"""Facet helpers for AgentConfig (Full Sweep F1 / ADR-003).

Singular resident applies per active harness binding. ``facet`` is preferred;
legacy rows may only have ``scope``. Keep dual-write until full collapse.
"""

from __future__ import annotations

from typing import Any, Optional


def effective_facet(agent_config: Any) -> str:
    """Return the facet discriminator for an AgentConfig-like object."""
    facet = getattr(agent_config, "facet", None)
    if isinstance(facet, str) and facet.strip():
        return facet.strip()
    scope = getattr(agent_config, "scope", None)
    if isinstance(scope, str) and scope.strip():
        return scope.strip()
    return "personal"


def sync_facet_with_scope(
    *,
    scope: str,
    facet: Optional[str] = None,
) -> tuple[str, str]:
    """Return ``(scope, facet)`` keeping both in sync for dual-write paths."""
    s = (scope or "personal").strip() or "personal"
    f = (facet or s).strip() or s
    return s, f
