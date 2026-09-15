"""Workspace-scope context for agent tool execution.

Tools accept an optional ``scope`` dict that constrains their reads/writes
to one workspace:

    {"kind": "workspace", "workspace_id": "<workspace id>"}

The scope is resolved from the inbound HTTP request — the frontend sends
``X-Integral-Scope: ws:<workspace_id>`` on every tool invocation so the
agent does not have to know about it. When the header is absent, scope is
left as ``None`` and the tool falls back to its prior unconstrained
behaviour (used by background / system callers that legitimately span
everything).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


from app.services.scope_header import (  # noqa: E402,F401 — re-export for agentive callers
    parse_scope_header,
    resolve_scope_from_request,
)


def _effective_workspace_id(item: Any) -> Optional[str]:
    """Return the workspace_id a Track/App resolves to.

    Direct ``workspace_id`` wins. Tracks inside an App inherit from the
    App's ``workspace_id`` if their own is empty.
    """
    direct = getattr(item, "workspace_id", None)
    if direct:
        return str(direct)
    app_node = getattr(item, "app", None)
    if app_node is not None:
        inherited = getattr(app_node, "workspace_id", None)
        if inherited:
            return str(inherited)
    if isinstance(item, dict):
        d = item.get("workspace_id")
        if d:
            return str(d)
        sp = item.get("app")
        if isinstance(sp, dict):
            inh = sp.get("workspace_id")
            if inh:
                return str(inh)
    return None


def matches_scope(item: Any, scope: Optional[Dict[str, Any]]) -> bool:
    """True iff the item is visible from ``scope`` (None means unconstrained)."""
    if not scope:
        return True
    if scope.get("kind") == "workspace":
        target = scope.get("workspace_id") or ""
        return bool(target) and _effective_workspace_id(item) == target
    return True


def filter_by_scope(items: List[Any], scope: Optional[Dict[str, Any]]) -> List[Any]:
    """Apply matches_scope() to every item."""
    if not scope:
        return list(items)
    return [it for it in items if matches_scope(it, scope)]


def workspace_id_from_scope(scope: Optional[Dict[str, Any]]) -> Optional[str]:
    """Return the canonical workspace_id from a scope dict, or None."""
    if not scope:
        return None
    if scope.get("kind") == "workspace":
        ws = scope.get("workspace_id")
        return str(ws) if ws else None
    return None


# REMOVED: resolve_target_workspace_id(parameters, scope)
#
# It had no callers, and its documented convention was "an explicit
# parameters['workspace_id'] always wins (agents may target a specific
# workspace by id)" -- i.e. an agent-supplied workspace id outranking the bound
# scope, with no membership check anywhere in the path. That is exactly the
# cross-workspace bypass closed in app/agentive/tooling/invoke.py (see
# tests/test_agentive_scope_enforcement.py), left sitting as a ready-made
# helper for whoever wired it up next.
#
# If a dispatcher genuinely needs to target a workspace other than the bound
# scope, it must validate membership for the principal first -- resolve through
# app/services/request_scope.py rather than trusting a parameter.


def scope_label(scope: Optional[Dict[str, Any]]) -> str:
    """Human label for error messages / system prompts."""
    if not scope:
        return "All workspaces"
    if scope.get("kind") == "workspace":
        return f"Workspace {scope.get('workspace_id') or '?'}"
    return "Unknown"
