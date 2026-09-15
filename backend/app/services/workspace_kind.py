"""Canonical workspace-kind helpers.

A workspace is either ``"personal"`` (single owner, no member pool) or
``"collaborative"`` (owner + member pool via ``IS_MEMBER_OF``, invitations,
storage quota). ``"organization"`` is the legacy spelling of the collaborative
kind — recognized on read so pre-rename rows keep working until migrated.

Keep all kind comparisons going through :func:`is_collaborative_kind` (or
:func:`workspace_is_collaborative` for a node) rather than literal
``== "organization"`` checks, so the rename stays in exactly one place.
"""

from __future__ import annotations

from typing import Any, Optional

#: Canonical stored kind for a multi-member workspace.
COLLABORATIVE_KIND = "collaborative"
#: Canonical stored kind for a single-owner workspace.
#: Stored ``kind`` values that mean "collaborative" (incl. the legacy spelling).
_COLLABORATIVE_KINDS = frozenset({COLLABORATIVE_KIND, "organization"})


def is_collaborative_kind(kind: Optional[str]) -> bool:
    """True when ``kind`` denotes a collaborative (multi-member) workspace."""
    return (kind or "").strip().lower() in _COLLABORATIVE_KINDS


def workspace_is_collaborative(ws: Any) -> bool:
    """True when the given Workspace node is collaborative-kind."""
    return bool(ws) and is_collaborative_kind(getattr(ws, "kind", None))
