"""Authorize local navigation requests without changing workspace data."""

from urllib.parse import quote

from app.models.nodes import ChatThread
from app.schemas.policy import PolicyAction, Resource, Subject
from app.services.agent_scope import (
    current_chat_thread_id,
    current_page_context,
    current_page_context_bound,
    current_scope_workspace_id,
)
from app.services.policy_engine import evaluate
from app.services.resource_location import DATA_APP_STATES, locate_resource


async def open_view_for_dispatch(
    *, user_id: str, target_kind: str, target_id: str, view_id: str | None = None
) -> dict:
    """Prepare a permitted destination for the current browser conversation."""
    workspace_id = current_scope_workspace_id.get()
    thread_id = current_chat_thread_id.get()
    thread = await ChatThread.get(thread_id) if thread_id else None
    if (
        not current_page_context_bound.get()
        or not current_page_context.get()
        or not workspace_id
        or thread is None
        or thread.user_id != user_id
        or thread.workspace_id != workspace_id
    ):
        return {
            "error": "browser_unavailable",
            "message": "No active chat view to open.",
        }

    if target_kind not in {"app", "track", "entry"}:
        return {
            "error": "invalid_target",
            "message": "Choose an app, collection or record.",
        }
    if view_id and target_kind != "track":
        return {
            "error": "invalid_view",
            "message": "Saved views belong to collections.",
        }

    location = await locate_resource(
        target_kind, target_id, allowed_app_states=DATA_APP_STATES
    )
    if location is None or location.workspace_id != workspace_id:
        return {
            "error": "unavailable",
            "message": "That destination isn't available here.",
        }
    actions: dict[str, PolicyAction] = {
        "app": "app.read",
        "track": "track.read",
        "entry": "entry.read",
    }
    resource_scope = (
        f"app:{target_id}" if target_kind == "app" else f"track:{location.track_id}"
    )
    decision = await evaluate(
        subject=Subject(kind="human", id=user_id),
        action=actions[target_kind],
        resource=Resource(kind=target_kind, id=target_id, scope=resource_scope),
    )
    if not decision.allowed:
        return {
            "error": "unavailable",
            "message": "That destination isn't available here.",
        }

    paths = {"app": "apps", "track": "tracks", "entry": "entries"}
    path = f"/{paths[target_kind]}/{quote(target_id, safe='')}"
    if target_kind == "entry" and location.track_id:
        path = f"/tracks/{quote(location.track_id, safe='')}?entry={quote(target_id, safe='')}"
    if view_id:
        from app.services.view_scope import read_scoped_view

        located_view = await read_scoped_view(view_id)
        if (
            located_view is None
            or located_view[1].track_id != target_id
            or located_view[1].workspace_id != workspace_id
            or getattr(located_view[0], "hidden", False)
        ):
            return {
                "error": "unavailable",
                "message": "That view isn't available here.",
            }
        view_decision = await evaluate(
            subject=Subject(kind="human", id=user_id),
            action="view.read",
            resource=Resource(kind="view", id=view_id, scope=resource_scope),
        )
        if not view_decision.allowed:
            return {
                "error": "unavailable",
                "message": "That view isn't available here.",
            }
        path += f"?view_id={quote(view_id, safe='')}"
    return {
        "ok": True,
        "navigation": {
            "path": path,
            "thread_id": thread_id,
            "workspace_id": workspace_id,
        },
        "message": "Ready to open after this reply. Reply with a brief destination link so the user can also open it later. Do not claim browser navigation has already completed.",
    }
