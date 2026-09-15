"""Unified collaborator / exclusion / access API for App, Track, Entry.

Phase 2 endpoints — every share-related mutation flows through
``app.services.sharing`` so the auto-guest workspace-membership rule,
notification emission, and change-event audit trail behave identically
across resource types.

* ``POST   /{apps|tracks|entries}/{id}/collaborators``
* ``DELETE /{apps|tracks|entries}/{id}/collaborators/{user_id}``
* ``POST   /{apps|tracks|entries}/{id}/exclusions``
* ``DELETE /{apps|tracks|entries}/{id}/exclusions/{user_id}``
* ``GET    /{apps|tracks|entries}/{id}/access``

The Track-side endpoints in ``api/tracks.py`` remain (back-compat) but
delegate to ``app.services.sharing`` under the hood. The endpoints here
fill the App and Entry gaps and add the unified ``/access`` listing.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import Request
from jvspatial.api import endpoint

from app.api.errors import MissingAuthenticationError
from app.api.utils import resolve_principal_id
from app.services.sharing import (
    add_collaborator,
    add_exclusion,
    list_access,
    remove_collaborator,
    remove_exclusion,
    update_collaborator_role,
)


def _require_user(request: Request) -> str:
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    return user_id


# ---------------------------------------------------------------------------
# App — exclusions (collaborators already wired in api/apps.py)
# ---------------------------------------------------------------------------


@endpoint(
    "/apps/{app_id}/exclusions",
    methods=["POST"],
    auth=True,
    tags=["Apps"],
)
async def add_space_exclusion(
    request: Request,
    app_id: str,
    user_id_to_exclude: str,
    reason: str = "",
) -> Dict[str, Any]:
    """Exclude one user from inherited (workspace/parent) access to an App."""
    user_id = _require_user(request)
    return await add_exclusion(user_id, "app", app_id, user_id_to_exclude, reason)


@endpoint(
    "/apps/{app_id}/exclusions/{user_id_to_restore}",
    methods=["DELETE"],
    auth=True,
    tags=["Apps"],
)
async def remove_space_exclusion(
    request: Request,
    app_id: str,
    user_id_to_restore: str,
) -> Dict[str, Any]:
    """Drop EXCLUDED_FROM edge — restore inherited access to an App."""
    user_id = _require_user(request)
    return await remove_exclusion(user_id, "app", app_id, user_id_to_restore)


# ---------------------------------------------------------------------------
# Entry — collaborators + exclusions
# ---------------------------------------------------------------------------


@endpoint(
    "/entries/{entry_id}/collaborators",
    methods=["POST"],
    auth=True,
    tags=["Entries"],
)
async def add_entry_collaborator(
    request: Request,
    entry_id: str,
    collaborator_user_id: str,
    role: str = "viewer",
) -> Dict[str, Any]:
    """Add COLLABORATES_ON{role} edge from user → entry."""
    user_id = _require_user(request)
    return await add_collaborator(
        user_id, "entry", entry_id, collaborator_user_id, role
    )


@endpoint(
    "/entries/{entry_id}/collaborators/{collaborator_user_id}",
    methods=["DELETE"],
    auth=True,
    tags=["Entries"],
)
async def remove_entry_collaborator(
    request: Request,
    entry_id: str,
    collaborator_user_id: str,
) -> Dict[str, Any]:
    """Drop the direct collaborator edge on this Entry."""
    user_id = _require_user(request)
    return await remove_collaborator(user_id, "entry", entry_id, collaborator_user_id)


@endpoint(
    "/entries/{entry_id}/collaborators/{collaborator_user_id}",
    methods=["PATCH"],
    auth=True,
    tags=["Entries"],
)
async def update_entry_collaborator_role(
    request: Request,
    entry_id: str,
    collaborator_user_id: str,
    role: str,
) -> Dict[str, Any]:
    """Change an existing direct collaborator's role on an Entry (owner only).

    Accepts ``admin | editor | commenter | viewer``. Emits
    ``entry.collaborator_role_update`` ChangeEvent.
    """
    user_id = _require_user(request)
    return await update_collaborator_role(
        user_id, "entry", entry_id, collaborator_user_id, role
    )


@endpoint(
    "/entries/{entry_id}/exclusions",
    methods=["POST"],
    auth=True,
    tags=["Entries"],
)
async def add_entry_exclusion(
    request: Request,
    entry_id: str,
    user_id_to_exclude: str,
    reason: str = "",
) -> Dict[str, Any]:
    """Exclude one user from inherited (Track/App) access to an Entry."""
    user_id = _require_user(request)
    return await add_exclusion(user_id, "entry", entry_id, user_id_to_exclude, reason)


@endpoint(
    "/entries/{entry_id}/exclusions/{user_id_to_restore}",
    methods=["DELETE"],
    auth=True,
    tags=["Entries"],
)
async def remove_entry_exclusion(
    request: Request,
    entry_id: str,
    user_id_to_restore: str,
) -> Dict[str, Any]:
    """Drop EXCLUDED_FROM edge — restore inherited access to an Entry."""
    user_id = _require_user(request)
    return await remove_exclusion(user_id, "entry", entry_id, user_id_to_restore)


# ---------------------------------------------------------------------------
# Unified access snapshot (drives ManageAccessModal)
# ---------------------------------------------------------------------------


@endpoint(
    "/apps/{app_id}/access",
    methods=["GET"],
    auth=True,
    tags=["Apps"],
)
async def get_space_access(request: Request, app_id: str) -> Dict[str, Any]:
    """Return ``{direct, inherited, excluded, links}`` for an App."""
    user_id = _require_user(request)
    return await list_access(user_id, "app", app_id)


@endpoint(
    "/tracks/{track_id}/access",
    methods=["GET"],
    auth=True,
    tags=["Tracks"],
)
async def get_track_access(request: Request, track_id: str) -> Dict[str, Any]:
    """Return ``{direct, inherited, excluded, links}`` for a Track."""
    user_id = _require_user(request)
    return await list_access(user_id, "track", track_id)


@endpoint(
    "/entries/{entry_id}/access",
    methods=["GET"],
    auth=True,
    tags=["Entries"],
)
async def get_entry_access(request: Request, entry_id: str) -> Dict[str, Any]:
    """Return ``{direct, inherited, excluded, links}`` for an Entry."""
    user_id = _require_user(request)
    return await list_access(user_id, "entry", entry_id)
