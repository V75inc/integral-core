"""Per-turn agent scope binding.

The chat router sets ``current_scope_workspace_id`` once per request from
the ``X-Integral-Scope`` header. The in-process agent action
(``EmbeddedIntegralAction``) reads this ContextVar in ``_stub_request``
and re-emits ``X-Integral-Scope: ws:<id>`` on every backend handler
call so the agent's read/list tools (list_tracks, query_entries, etc.)
filter by the workspace the user has selected in the UI rather than
falling back to the user's Personal Workspace.

Why a ContextVar (not a module attribute):
- The chat runtime is multi-tenant; concurrent turns from different
  users must not bleed into each other.
- ``asyncio`` preserves ContextVar values across ``await``s within the
  same Task, which matches the embed call graph (chat router →
  jvagent.embed → InteractWalker → cockpit → skill execute → action).
- A token-based ``set`` / ``reset`` keeps the binding strictly scoped
  to one turn.

Defined here (not on the action module) so both backend services and
the agent-side action can import from a stable, well-known path. The
action module ships under the jvagent app dir and is loaded via
``spec_from_file_location``, so its import name is not stable enough
to depend on from the backend side.
"""

from __future__ import annotations

import contextvars
from typing import Any, Dict, List, Optional, TypeVar

from app.models.nodes import App, Track

current_scope_workspace_id: contextvars.ContextVar[Optional[str]] = (
    contextvars.ContextVar("integral_agent_scope_workspace_id", default=None)
)

current_focused_track_id: contextvars.ContextVar[Optional[str]] = (
    contextvars.ContextVar("integral_agent_focused_track_id", default=None)
)

current_focused_view_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "integral_agent_focused_view_id", default=None
)

# Full client page_context snapshot for the active chat turn (hybrid stub+tool).
current_page_context: contextvars.ContextVar[Optional[Dict[str, Any]]] = (
    contextvars.ContextVar("integral_agent_page_context", default=None)
)

# ChatThread id for the active turn — lets tools load last_page_context later.
current_chat_thread_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "integral_agent_chat_thread_id", default=None
)

_T = TypeVar("_T")


def active_workspace_id() -> Optional[str]:
    """Return the bound active workspace id, or None for legacy union scope."""
    return current_scope_workspace_id.get()


def filter_nodes_by_active_workspace(
    items: List[_T],
    workspace_id: Optional[str] = None,
) -> List[_T]:
    """B-AGENT-03 layer-1 gate: narrow a node list to one workspace.

    When ``workspace_id`` is omitted, reads :data:`active_workspace_id`.
    When both are unset, returns ``items`` unchanged (cross-workspace union).
    """
    from app.services.request_scope import matches_workspace

    ws = workspace_id if workspace_id is not None else active_workspace_id()
    if not ws:
        return list(items)
    return [it for it in items if matches_workspace(it, ws)]


async def accessible_tracks_for_scope(
    user_id: str,
    *,
    workspace_id: Optional[str] = None,
) -> List[Track]:
    """User-accessible tracks narrowed to the active workspace when bound."""
    from app.services.permissions import get_user_accessible_tracks

    tracks = await get_user_accessible_tracks(user_id)
    return filter_nodes_by_active_workspace(tracks, workspace_id)


async def accessible_apps_for_scope(
    user_id: str,
    *,
    workspace_id: Optional[str] = None,
) -> List[App]:
    """User-accessible apps narrowed to the active workspace when bound."""
    from app.services.permissions import get_user_accessible_apps

    apps = await get_user_accessible_apps(user_id)
    return filter_nodes_by_active_workspace(apps, workspace_id)


def scope_violation_envelope(message: str) -> Dict[str, Any]:
    """Standard executor error when a target is outside the active workspace."""
    return {
        "error": True,
        "status_code": 403,
        "error_code": "scope_violation",
        "message": message,
    }


async def check_track_in_active_scope(
    track_id: str,
    *,
    user_id: str,
    workspace_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Return an error envelope if ``track_id`` is outside the scoped universe."""
    ws = workspace_id if workspace_id is not None else active_workspace_id()
    if not ws or not track_id:
        return None
    scoped = await accessible_tracks_for_scope(user_id, workspace_id=ws)
    if any(t.id == track_id for t in scoped):
        return None
    return scope_violation_envelope(
        f"Track {track_id!r} is outside the active workspace {ws!r}."
    )


async def check_entry_in_active_scope(
    entry_id: str,
    *,
    user_id: str,
    workspace_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Return an error envelope if the entry's parent track is out of scope."""
    if not entry_id:
        return None
    from app.models.nodes import Entry

    entry = await Entry.get(entry_id)
    if entry is None:
        return None
    track_id = getattr(entry, "track_id", "") or ""
    if not track_id:
        return None
    return await check_track_in_active_scope(
        track_id, user_id=user_id, workspace_id=workspace_id
    )


async def check_resource_in_active_scope(
    resource_type: str,
    resource_id: str,
    *,
    user_id: str,
    workspace_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Gate share/collaborator targets to the active workspace."""
    if not resource_id:
        return scope_violation_envelope("resource_id is required")
    if resource_type == "track":
        return await check_track_in_active_scope(
            resource_id, user_id=user_id, workspace_id=workspace_id
        )
    if resource_type == "app":
        ws = workspace_id if workspace_id is not None else active_workspace_id()
        if not ws:
            return None
        from app.models.nodes import App as WorkspaceApp

        app = await WorkspaceApp.get(resource_id)
        if app is None:
            return None
        from app.services.request_scope import matches_workspace

        if matches_workspace(app, ws):
            return None
        return scope_violation_envelope(
            f"App {resource_id!r} is outside the active workspace {ws!r}."
        )
    if resource_type == "entry":
        return await check_entry_in_active_scope(
            resource_id, user_id=user_id, workspace_id=workspace_id
        )
    return None
