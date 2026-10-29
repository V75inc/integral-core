"""Bounded, current-access navigation for background-task context panels."""

from __future__ import annotations

from urllib.parse import quote

from app.exceptions import AuthorizationUnavailableError
from app.models.nodes import App, Entry, Track
from app.services.permissions import resolve_role
from app.services.resource_location import DATA_APP_STATES, locate_resource


async def task_context_links(
    refs: list[dict], *, user_id: str, workspace_id: str
) -> list[dict[str, str]]:
    """Resolve declared targets on demand; a task is never authority to read them.

    This is an optional detail projection, not the polled task list. Deduplicate
    and cap references before reads; never scan a workspace or invoke a model.
    """
    links = []
    seen = set()
    models = {"app": App, "track": Track, "entry": Entry}
    candidates = []
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        kind = ref.get("resource_type")
        identifier = ref.get("resource_id")
        if (
            not isinstance(kind, str)
            or kind not in models
            or not isinstance(identifier, str)
            or not identifier
            or len(identifier) > 255
            or (kind, identifier) in seen
        ):
            continue
        seen.add((kind, identifier))
        candidates.append((kind, identifier))
        if len(candidates) >= 12:
            break
    for kind, identifier in candidates:
        try:
            location = await locate_resource(
                kind, identifier, allowed_app_states=DATA_APP_STATES
            )
            if (
                location is None
                or location.workspace_id != workspace_id
                or await resolve_role(user_id, kind, identifier) is None
            ):
                continue
            node = await models[kind].get(identifier)
            if node is None:
                continue
            route = {"app": "apps", "track": "tracks", "entry": "entries"}[kind]
            href = f"/{route}/{quote(identifier, safe='')}"
            links.append(
                {
                    "kind": kind,
                    "id": identifier,
                    "label": str(
                        getattr(node, "name", "")
                        or getattr(node, "title", "")
                        or kind.capitalize()
                    ),
                    "href": href,
                }
            )
        except AuthorizationUnavailableError:
            # Missing navigation must not hide the task's pause/recovery controls.
            # Do not publish a label or inferred destination without current proof.
            continue
    return links
