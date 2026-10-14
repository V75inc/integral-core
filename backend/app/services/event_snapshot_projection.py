"""Bounded, recipient-specific projection of stored event snapshot blocks.

Snapshot labels cannot safely be separated from their embedded references.
Withhold the whole block when a reference is unavailable, foreign or denied.
This is not a field-level permission system or a natural-language DLP filter.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from app.services.change_event_logger import ChangeEventEnvelope

MAX_VALUES = 1000
MAX_REFERENCES = 100
MAX_DEPTH = 20
MAX_TEXT = 65536
MAX_TOTAL_TEXT = 131072
REFERENCE = re.compile(r"n\.([A-Za-z][A-Za-z0-9_]*)\.[A-Za-z0-9_-]+")
KINDS = {
    "Entry": "entry",
    "Track": "track",
    "WorkspaceApp": "app",
    "App": "app",
    "View": "view",
    "Comment": "comment",
    "Attachment": "attachment",
    "User": "user",
    "Workspace": "workspace",
    "OperationalModel": "operational_model",
    "Connector": "connector",
}


def snapshot_references(
    value: Any, *, content_fields: frozenset[str] = frozenset()
) -> set[tuple[str, str]] | None:
    """None means an incomplete or undeclared reference inspection."""
    pending = [(value, 0, True)]
    references: set[tuple[str, str]] = set()
    visited = 0
    text_size = 0
    while pending:
        item, depth, inspect_references = pending.pop()
        visited += 1
        if visited > MAX_VALUES or depth > MAX_DEPTH:
            return None
        if isinstance(item, dict):
            if len(item) > MAX_VALUES - visited:
                return None
            for key, part in item.items():
                pending.append((key, depth + 1, True))
                inspect = not (
                    depth == 0 and key in content_fields and isinstance(part, str)
                )
                pending.append((part, depth + 1, inspect))
        elif isinstance(item, list):
            if len(item) > MAX_VALUES - visited:
                return None
            pending.extend((part, depth + 1, True) for part in item)
        elif isinstance(item, str):
            text_size += len(item)
            if len(item) > MAX_TEXT or text_size > MAX_TOTAL_TEXT:
                return None
            if not inspect_references:
                continue
            for match in REFERENCE.finditer(item):
                kind = KINDS.get(match.group(1))
                if kind is None:
                    return None
                references.add((kind, match.group()))
                if len(references) > MAX_REFERENCES:
                    return None
        elif item is not None and not isinstance(item, (bool, int, float)):
            return None
    return references


async def reference_workspace(kind: str, resource_id: str) -> str | None:
    """Resolve current direct-resource or unique Entry-owned reference scope."""
    from jvspatial.core.context import get_default_context

    from app.models.edges import HAS_ATTACHMENT, HAS_COMMENT
    from app.models.nodes import Attachment, Comment
    from app.services.authority_facts import primary_node_record
    from app.services.event_history import event_workspace_id

    if kind not in {"comment", "attachment"}:
        return await event_workspace_id(f"{kind}:{resource_id}")
    model, edge = (
        (Comment, HAS_COMMENT) if kind == "comment" else (Attachment, HAS_ATTACHMENT)
    )
    context = get_default_context()
    row = await primary_node_record(context.database, model, resource_id)
    if row is None:
        return None
    node = await context._deserialize_entity(model, row)
    parents = await node.nodes(edge=[edge], direction="in", node=["Entry"], limit=2)
    if len(parents) != 1:
        return None
    return await event_workspace_id(f"entry:{parents[0].id}")


async def project_event_snapshots(
    user_id: str,
    event: ChangeEventEnvelope,
    payload: dict[str, Any],
    *,
    evaluate: Any,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """Call only inside release admission; never share results across recipients."""
    from app.services.event_access import _can_release_event_current
    from app.services.event_history import event_workspace_id

    projected = dict(payload)
    if workspace_id is None:
        workspace_id = await event_workspace_id(event.scope)
    admitted: dict[tuple[str, str], bool] = {}
    for key in ("before", "after", "details"):
        value = payload.get(key)
        content_fields: frozenset[str] = frozenset()
        if (
            key in {"before", "after"}
            and str(event.resource_type).casefold() == "comment"
            and isinstance(value, dict)
        ):
            # Comment text is authorized primary-record content, not a graph
            # expansion. Its author ID is the same public metadata returned by
            # the Comments API under the current directory contract. Never
            # exempt a nested author object or an undeclared identity kind.
            content_fields = frozenset({"text"})
            author = value.get("author_id")
            if isinstance(author, str) and (
                author == "public" or re.fullmatch(r"n\.User\.[A-Za-z0-9_-]+", author)
            ):
                content_fields |= frozenset({"author_id"})
        references = snapshot_references(value, content_fields=content_fields)
        safe = references is not None
        for kind, resource_id in sorted(references or ()):
            ref = (kind, resource_id)
            if ref not in admitted:
                if len(admitted) >= MAX_REFERENCES:
                    safe = False
                    break
                # Reuse the actual resource audience, not the main record ACL.
                candidate = replace(
                    event,
                    resource_type=kind,
                    resource_id=resource_id,
                    action="snapshot.read",
                    before=None,
                    after=None,
                    details=None,
                )
                reference_workspace_id = await reference_workspace(kind, resource_id)
                # Parent-owned references without a direct canonical resolver
                # are withheld unless they are the already-authorized primary.
                primary = resource_id == event.resource_id
                same_workspace = (
                    primary
                    or (
                        workspace_id is not None
                        and reference_workspace_id == workspace_id
                    )
                    or (
                        kind in {"user", "connector", "attachment"}
                        and event.scope.startswith("user:")
                    )
                )
                admitted[ref] = bool(
                    same_workspace
                    and await _can_release_event_current(
                        user_id,
                        candidate,
                        action="event_feed.subscribe",
                        evaluate=evaluate,
                    )
                )
            if not admitted[ref]:
                safe = False
                break
        if not safe:
            projected[key] = None
    return projected
