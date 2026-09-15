"""Threads request-scope workspace_id into a contextvar so deep
agentive call paths can read it without explicit parameter plumbing.

Reads X-Integral-Scope header (already validated upstream by
services/request_scope.py); stores workspace_id for the duration of
the request.
"""

from contextvars import ContextVar
from typing import Optional

_current_scope_key: ContextVar[Optional[str]] = ContextVar(
    "_current_scope_key", default=None
)


def set_scope_key(workspace_id: Optional[str]) -> None:
    """Store ``workspace_id`` on the request-scoped contextvar."""
    _current_scope_key.set(workspace_id)


def get_scope_key() -> Optional[str]:
    """Return the workspace_id stored for the current request scope (or None)."""
    return _current_scope_key.get()
