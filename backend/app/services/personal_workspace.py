"""Personal Workspace lifecycle helpers.

A Personal Workspace is auto-created at signup
(``app/api/auth.py::signup``) so every authenticated User starts with
exactly one. The helpers below are idempotent — safe to call lazily on
first read, from the migration script, or anywhere else that needs the
caller's Personal Workspace to exist.

Invariant: at most one ``Workspace{kind:"personal"}`` per User, expressed
graph-natively as a single ``User —IS_MEMBER_OF{role:"owner"}→ Workspace``
edge. The idempotence check walks that edge rather than querying a
denormalized field.
"""

from __future__ import annotations

import logging
from typing import Optional

from app.models.edges import IS_MEMBER_OF
from app.models.nodes import User, Workspace
from app.services.app_graph import catalog_workspace
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)


async def _find_personal_workspace(user: User) -> Optional[Workspace]:
    """Return the user's existing Personal Workspace, or ``None``."""
    try:
        candidates = await user.nodes(edge=[IS_MEMBER_OF], node=["Workspace"])
    except Exception:
        logger.exception("_find_personal_workspace traversal failed for %s", user.id)
        return None
    for ws in candidates:
        if getattr(ws, "kind", "") == "personal":
            return ws
    return None


async def ensure_personal_workspace(user: User) -> Workspace:
    """Return the user's Personal Workspace, creating it if missing.

    Idempotent — concurrent first-reads of the same User may both enter;
    the second observes the first's write via the edge traversal and
    short-circuits.
    """
    existing = await _find_personal_workspace(user)
    if existing:
        return existing
    now = utc_now_iso()
    name = (user.display_name or "").strip() or "Personal"
    workspace = await Workspace.create(
        kind="personal",
        workspace_type="personal",
        name=name,
        name_fold=name.casefold(),
        description="Your private workspace.",
        accent_color="",
        created_at=now,
        updated_at=now,
    )
    await user.connect(
        workspace,
        edge=IS_MEMBER_OF,
        role="owner",
        joined_at=now,
        can_create_apps=True,
        can_create_tracks=True,
    )
    try:
        await catalog_workspace(workspace)
    except Exception:
        logger.exception(
            "catalog_workspace failed for personal workspace %s",
            getattr(workspace, "id", ""),
        )
    return workspace


async def ensure_personal_workspace_for_user_id(user_id: str) -> Optional[Workspace]:
    """Ensure a personal workspace exists for a User or AuthUser id.

    Convenience wrapper that resolves either id shape via
    ``app.services.permissions.get_user_node``.
    """
    from app.services.permissions import get_user_node

    user = await get_user_node(user_id)
    if not user:
        return None
    return await ensure_personal_workspace(user)
