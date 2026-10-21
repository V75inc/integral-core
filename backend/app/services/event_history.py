"""Bounded event history with current resource and request workspace gates."""

from __future__ import annotations

from typing import Any

from app.middleware.permissions_cache import isolated_permissions_cache
from app.services.change_event_logger import envelope_from_dblog
from app.services.event_access import prepare_event_payload
from app.services.event_history_cursor import (
    decode_history_cursor,
    encode_history_cursor,
)
from app.services.pagination import encode_cursor
from app.services.policy_engine import evaluate
from app.services.resource_location import locate_resource

MAX_CANDIDATES = 1000
WINDOW_SIZE = 100


async def event_workspace_id(scope: str) -> str | None:
    """Derive workspace identity from current containment, never a snapshot."""
    kind, separator, resource_id = scope.partition(":")
    if not separator or not resource_id:
        return None
    if kind in {"ws", "workspace"}:
        from app.models.nodes import Workspace

        workspace = await Workspace.get(resource_id)
        return workspace.id if workspace is not None else None
    if kind == "space":
        kind = "app"
    if kind in {"app", "track", "entry", "view"}:
        location = await locate_resource(kind, resource_id)
        return location.workspace_id if location is not None else None
    return None


async def _in_workspace(event: Any, user_id: str, workspace_id: str) -> bool:
    with isolated_permissions_cache():
        if event.scope.startswith("user:"):
            # Account-level history belongs to the caller's Personal workspace.
            from app.models.nodes import Workspace
            from app.services.permissions import get_user_node

            workspace = await Workspace.get(workspace_id)
            user = await get_user_node(user_id)
            return bool(
                workspace is not None
                and (
                    workspace.kind == "personal"
                    or str(event.resource_type).casefold() == "notification"
                )
                and user is not None
                and event.scope
                in {f"user:{user_id}", f"user:{user.id}", f"user:{user.user_id}"}
            )
        return await event_workspace_id(event.scope) == workspace_id


async def read_event_history(
    *,
    logger: Any,
    user_id: str,
    workspace_id: str,
    action: str,
    cursor: str | None,
    limit: int,
    descending: bool,
    scope: str | None = None,
    actor_kind: str | None = None,
    event_action: str | None = None,
    resource_id: str | None = None,
    resource_type: str | None = None,
) -> dict[str, Any]:
    """Walk at most 1000 candidates; a cursor can advance through denied rows.

    ``has_more`` means more candidates may exist, not that another authorized
    result is promised. No full-history count is fabricated. An empty bounded
    window with continuation is distinct from the end of accessible history.
    """
    events: list[dict[str, Any]] = []
    scanned = 0
    binding = {
        "principal_id": user_id,
        "workspace_id": workspace_id,
        "action": action,
        "descending": descending,
        "scope": scope or "",
        "actor_kind": actor_kind or "",
        "event_action": event_action or "",
        "resource_id": resource_id or "",
        "resource_type": resource_type or "",
    }
    position = decode_history_cursor(cursor, binding=binding) if cursor else None
    scope_options: dict[str, Any] = {}
    if scope and scope.startswith("user:"):
        from app.services.permissions import get_user_node

        caller = await get_user_node(user_id)
        own_ids = {user_id}
        if caller is not None:
            own_ids.update(
                identity for identity in (caller.id, caller.user_id) if identity
            )
        if scope in {f"user:{identity}" for identity in own_ids}:
            scope_options["scope_aliases"] = sorted(
                f"user:{identity}" for identity in own_ids
            )
    exhausted = False
    while len(events) < limit and scanned < MAX_CANDIDATES:
        rows = await logger.find_page(
            cursor=position,
            limit=min(WINDOW_SIZE, MAX_CANDIDATES - scanned),
            descending=descending,
            scope=scope,
            actor_kind=actor_kind,
            workspace_id=workspace_id,
            event_action=event_action,
            resource_id=resource_id,
            resource_type=resource_type,
            **scope_options,
        )
        if not rows:
            exhausted = True
            break
        for row in rows:
            event = envelope_from_dblog(row)
            scanned += 1
            position = encode_cursor(event.id, event.ts)
            if await _in_workspace(event, user_id, workspace_id):
                payload = await prepare_event_payload(
                    user_id, event, action=action, evaluate=evaluate, flat=True
                )
                if payload is not None:
                    events.append(payload)
            if len(events) == limit or scanned == MAX_CANDIDATES:
                break
    return {
        "events": events,
        "next_cursor": (
            None
            if exhausted or position is None
            else encode_history_cursor(position, binding=binding)
        ),
        "has_more": not exhausted,
        "scan_complete": exhausted,
    }
