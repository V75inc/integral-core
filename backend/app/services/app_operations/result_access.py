"""Current dependency admission for replayed operation response disclosure."""

from __future__ import annotations

from typing import Any, Dict

from app.api.errors import InsufficientPermissionsError
from app.middleware.permissions_cache import isolated_permissions_cache
from app.models.edges import CONTAINS
from app.models.nodes import App, Entry, Track
from app.services.permissions import resolve_role


async def authorize_replayed_result(
    result: Dict[str, Any], *, user_id: str, workspace_id: str, app_id: str
) -> None:
    """Check stored record dependencies without re-executing a committed effect.

    A receipt establishes that an effect occurred; it cannot establish the
    current caller's authority to see its old response. Missing/deleted/foreign
    dependencies withhold the response. This check does not turn the existing
    decision fingerprint into a durable authorization epoch.
    """
    with isolated_permissions_cache():
        collections = [
            result.get("object_refs", []),
            (result.get("output") or {}).get("object_refs", []),
            (result.get("evidence") or {}).get("object_refs", []),
        ]
        if not any(collections):
            return
        app = await App.get(app_id)
        if (
            app is None
            or app.workspace_id != workspace_id
            or await resolve_role(user_id, "app", app_id) is None
        ):
            raise InsufficientPermissionsError(message="Operation result access denied")
        checked = set()
        for references in collections:
            if not isinstance(references, list):
                raise InsufficientPermissionsError(
                    message="Operation result access denied"
                )
            for reference in references:
                if not isinstance(reference, dict) or reference.get("kind") != "entry":
                    raise InsufficientPermissionsError(
                        message="Operation result access denied"
                    )
                entry_id = reference.get("id")
                if not entry_id:
                    raise InsufficientPermissionsError(
                        message="Operation result access denied"
                    )
                if entry_id in checked:
                    continue
                entry = await Entry.get(entry_id)
                track = (
                    await Track.get(entry.track_id)
                    if entry and entry.track_id
                    else None
                )
                if entry is None or track is None or track.workspace_id != workspace_id:
                    raise InsufficientPermissionsError(
                        message="Operation result access denied"
                    )
                context = await app.get_context()
                parents = await context.find_edges_between(
                    app_id, track.id, edge_class=CONTAINS
                )
                if (
                    not parents
                    or await resolve_role(user_id, "entry", entry_id) is None
                ):
                    raise InsufficientPermissionsError(
                        message="Operation result access denied"
                    )
                checked.add(entry_id)
