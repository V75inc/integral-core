"""Frontend route helpers for in-app notification click targets.

Mirrors ``frontend/src/utils/resourcePaths.ts`` so persisted
``Notification.action_url`` values align with React Router paths.
"""

from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import quote


def _track_path(track_id: str) -> str:
    return f"/tracks/{quote(track_id, safe='')}"


def _app_path(app_id: str) -> str:
    return f"/apps/{quote(app_id, safe='')}"


def _workspace_path(workspace_id: str) -> str:
    return f"/workspaces/{quote(workspace_id, safe='')}"


def entry_path(entry_id: str, track_id: str) -> str:
    """Canonical entry deep-link — opens the entry on its parent track."""
    base = _track_path(track_id)
    return f"{base}?entry={quote(entry_id, safe='')}"


def resolve_resource_action_url(
    resource_type: str,
    resource_id: str,
    *,
    track_id: Optional[str] = None,
) -> Optional[str]:
    """Map a substrate resource to its frontend route."""
    if not resource_type or not resource_id:
        return None
    kind = resource_type.strip().lower()
    if kind == "app":
        return _app_path(resource_id)
    if kind == "track":
        return _track_path(resource_id)
    if kind == "workspace":
        return _workspace_path(resource_id)
    if kind == "entry":
        tid = (track_id or "").strip()
        return entry_path(resource_id, tid) if tid else None
    return None


def resolve_notification_action_url(
    kind: str,
    payload: Dict[str, Any],
) -> Optional[str]:
    """Derive ``Notification.action_url`` from dispatch kind + payload."""
    explicit = payload.get("action_url")
    if explicit:
        return str(explicit)

    entry_id = str(payload.get("entry_id") or "").strip()
    track_id = str(payload.get("track_id") or "").strip()

    if kind == "mention":
        if entry_id and track_id:
            return entry_path(entry_id, track_id)
        if track_id:
            return _track_path(track_id)
        return None

    if kind == "entry_update":
        if entry_id and track_id:
            return entry_path(entry_id, track_id)
        if track_id:
            return _track_path(track_id)
        return None

    if kind == "agent_pending_write":
        return "/approvals"

    if kind == "invitation":
        invitation_id = str(payload.get("invitation_id") or "").strip()
        if invitation_id:
            return f"/invitations/received/{quote(invitation_id, safe='')}"
        workspace_id = str(payload.get("workspace_id") or "").strip()
        if workspace_id:
            return _workspace_path(workspace_id)
        if entry_id and track_id:
            return entry_path(entry_id, track_id)
        if track_id:
            return _track_path(track_id)
        return None

    resource_type = str(payload.get("resource_type") or "").strip()
    resource_id = str(payload.get("resource_id") or "").strip()
    if resource_type and resource_id:
        return resolve_resource_action_url(
            resource_type,
            resource_id,
            track_id=track_id or None,
        )

    return None
