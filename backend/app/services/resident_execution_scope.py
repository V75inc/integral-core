"""Current resident scope projection, separate from UI workspace preference."""

from __future__ import annotations

from typing import Any

from app.api.errors import InsufficientPermissionsError
from app.middleware.permissions_cache import isolated_permissions_cache
from app.models.nodes import Workspace
from app.services.permissions import get_user_node
from app.services.workspace_permissions import (
    can_access_workspace,
    member_creation_flags,
)


async def get_execution_scope_for_dispatch(
    *, user_id: str, workspace_id: str | None = None
) -> dict[str, Any]:
    """Expose only the dispatcher's workspace and freshly checked rights.

    The dispatcher supplies both identifiers; neither is a model argument.
    An absent or revoked binding never falls back to a saved preference or
    silently creates a Personal Workspace.
    """
    if not user_id or not workspace_id:
        raise InsufficientPermissionsError(message="Execution scope is unavailable")
    with isolated_permissions_cache():
        role = await can_access_workspace(user_id, workspace_id)
        workspace = await Workspace.get(workspace_id)
        if role == "none" or workspace is None:
            raise InsufficientPermissionsError(message="Execution scope is unavailable")
        if role == "owner":
            can_apps, can_tracks = True, True
        elif workspace.kind == "personal":
            can_apps, can_tracks = False, False
        else:
            user = await get_user_node(user_id)
            if user is None:
                raise InsufficientPermissionsError(
                    message="Execution scope is unavailable"
                )
            can_apps, can_tracks = await member_creation_flags(user, workspace_id)
        return {
            "user_id": user_id,
            "workspace": {
                "id": workspace.id,
                "name": workspace.name or "",
                "kind": workspace.kind,
            },
            "role": role,
            "can_create_apps": can_apps,
            "can_create_tracks": can_tracks,
        }
