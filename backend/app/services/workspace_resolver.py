"""Workspace resolution helpers.

A App/Track/Invitation is born into a single Workspace. Callers either
supply the target ``workspace_id`` directly (typical for creates inside an
organization workspace), or omit it and fall back to the caller's Personal
Workspace.
"""

from __future__ import annotations

import logging
from typing import Optional

from app.models.nodes import User, Workspace

logger = logging.getLogger(__name__)


async def resolve_workspace_id(
    *, user_id: str, workspace_id: Optional[str] = None
) -> str:
    """Resolve canonical ``workspace_id`` for a new App/Track/Invitation.

    ``workspace_id`` present → returned verbatim after a presence check.
    ``workspace_id`` absent  → caller's Personal Workspace id.

    Raises ``ValueError`` if no Workspace can be resolved.
    """
    from app.services.personal_workspace import ensure_personal_workspace_for_user_id

    if workspace_id:
        ws = await Workspace.get(workspace_id)
        if not ws:
            raise ValueError(f"Workspace {workspace_id} not found")
        return ws.id

    personal = await ensure_personal_workspace_for_user_id(user_id)
    if not personal:
        raise ValueError(f"No personal workspace for user {user_id}")
    return personal.id


async def resolve_workspace_id_for_user_node(
    *, user: User, workspace_id: Optional[str] = None
) -> str:
    """Convenience variant that takes a User node directly."""
    from app.services.personal_workspace import ensure_personal_workspace

    if workspace_id:
        ws = await Workspace.get(workspace_id)
        if not ws:
            raise ValueError(f"Workspace {workspace_id} not found")
        return ws.id

    personal = await ensure_personal_workspace(user)
    return personal.id
