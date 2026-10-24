"""Bounded event history with current resource and request workspace gates."""

from __future__ import annotations

from time import monotonic
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
MAX_SCAN_SECONDS = 1.0


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


async def _missing_primary_events(events: list[Any]) -> set[tuple[str, str]]:
    """Deny missing primaries in one flat read; existence never grants access.

    Do not prefilter policy-denial forensic events or connector tombstones:
    those have separate declared audiences. All surviving rows still pass
    the canonical containment, admission and snapshot projection gates.
    """
    from jvspatial.core.context import get_default_context

    from app.api.errors import QueryUnavailableError
    from app.models.nodes import App, Entry, Track, View

    models = {"entry": Entry, "track": Track, "app": App, "space": App, "view": View}
    selected = {
        (str(event.resource_type).casefold(), event.resource_id)
        for event in events
        if str(event.resource_type).casefold() in models
        and event.resource_id
        and not (
            event.action == "policy.deny"
            and event.before is None
            and event.after is None
        )
    }
    if not selected:
        return set()
    ids = sorted({resource_id for _, resource_id in selected})
    try:
        rows = await get_default_context().database.find(
            "node", {"id": {"$in": ids}}, limit=len(ids) + 1
        )
        found = {}
        for row in rows:
            node_id = row.get("id")
            if node_id not in ids or node_id in found:
                raise ValueError("Invalid primary existence result")
            found[node_id] = row.get("entity")
        return {
            (kind, node_id)
            for kind, node_id in selected
            if found.get(node_id) != models[kind]._entity_name()
        }
    except Exception as exc:
        raise QueryUnavailableError(message="Event history is unavailable") from exc


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
    started = monotonic()
    budget_spent = False
    while len(events) < limit and scanned < MAX_CANDIDATES and not budget_spent:
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
        candidates = [envelope_from_dblog(row) for row in rows]
        missing = await _missing_primary_events(candidates)
        for event in candidates:
            scanned += 1
            position = encode_cursor(event.id, event.ts)
            if (
                str(event.resource_type).casefold(),
                event.resource_id,
            ) not in missing and await _in_workspace(event, user_id, workspace_id):
                payload = await prepare_event_payload(
                    user_id, event, action=action, evaluate=evaluate, flat=True
                )
                if payload is not None:
                    events.append(payload)
            budget_spent = monotonic() - started >= MAX_SCAN_SECONDS
            if len(events) == limit or scanned == MAX_CANDIDATES or budget_spent:
                break
    continuation = (
        encode_history_cursor(position, binding=binding)
        if position is not None
        else None
    )
    return {
        "events": events,
        "next_cursor": None if exhausted else continuation,
        # Pagination ends at exhaustion; a live forward subscriber still needs
        # its last scanned position, including withheld rows. Preserve the same
        # encrypted principal/workspace/query binding as normal continuation.
        "resume_cursor": continuation if not descending else None,
        "has_more": not exhausted,
        "scan_complete": exhausted,
    }
