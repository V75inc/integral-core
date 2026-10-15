"""Comment API endpoints with threading support."""

import logging
from typing import Any, Dict, Optional

from fastapi import Request
from jvspatial.api import endpoint

from app.api.errors import (
    InsufficientPermissionsError,
    MissingAuthenticationError,
    ResourceNotFoundError,
)
from app.api.utils import attach_author_exports, export_node, resolve_principal_id
from app.models.edges import MENTIONS
from app.models.nodes import Entry
from app.schemas.policy import Resource, Subject
from app.services import notification_router
from app.services.mentions import resolve_mentions
from app.services.policy_engine import evaluate as policy_evaluate
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)


@endpoint("/entries/{entry_id}/comments", methods=["GET"], auth=True, tags=["Comments"])
async def get_entry_comments(
    request: Request,
    entry_id: str,
) -> Dict[str, Any]:
    """Get all comments for an entry."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    entry = await Entry.get(entry_id)
    if not entry:
        raise ResourceNotFoundError(message="Entry not found")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="entry.read",
        resource=Resource(
            kind="entry",
            id=entry_id,
            scope=f"track:{entry.track_id or ''}",
        ),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")

    comments, page = await entry.nodes_page(
        edge=["HAS_COMMENT"],
        node=["Comment"],
        sort=[("context.created_at", 1), ("id", 1)],
        limit=2000,
    )
    items = [await export_node(c) for c in comments]
    await attach_author_exports(items)
    # nodes_page already sorts; keep a stable Python tie-break only if
    # created_at was missing on older rows.
    items.sort(
        key=lambda x: (x.get("created_at") or "", x.get("id") or ""),
    )

    # Whether this caller may delete comments they did not write. Computed
    # here, from the same action ``delete_comment`` enforces, so the UI's
    # delete affordance cannot drift out of step with the gate: a client that
    # decided this for itself would either hide a control that works or offer
    # one that 403s. Public-share comments (``author_id="public"``) are the
    # case that makes it necessary — nobody is their author.
    can_moderate = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="comment.moderate",
        resource=Resource(
            kind="comment",
            id="",
            scope=f"track:{entry.track_id or ''}",
        ),
    )

    return {
        "comments": items,
        "total": len(items),
        "entry_id": entry_id,
        "can_moderate": can_moderate.allowed,
    }


@endpoint(
    "/entries/{entry_id}/comments", methods=["POST"], auth=True, tags=["Comments"]
)
async def create_comment(
    request: Request,
    entry_id: str,
    text: str,
    parent_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a comment on an entry, optionally as a reply to another comment."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    from app.services.comment_creation import create_comment_record

    created = await create_comment_record(
        user_id=user_id,
        entry_id=entry_id,
        text=text,
        parent_id=parent_id,
        request=request,
    )
    comment, entry, user = created.comment, created.entry, created.user
    now = comment.created_at or utc_now_iso()

    # Phase 9 NOTIF-02 acceptance criterion #3: parse @mentions from
    # comment text + dispatch notifications via the canonical router.
    # Resolution chain (services/mentions.resolve_mentions): jvspatial
    # id → user_id → display_name (case-insensitive) → email local-part
    # — so ``@alice`` works even when the underlying node id is
    # ``n.User.<uuid>``. MENTIONS edge connects the new Comment to each
    # mentioned User for substrate-native audit + retrieval.
    actor_name = ""
    if user:
        actor_name = (
            getattr(user, "display_name", "") or getattr(user, "email", "") or user_id
        )
    mentioned_users = await resolve_mentions(
        text or "",
        exclude_user_id=user_id,
        track_id=entry.track_id or None,
    )
    for mentioned in mentioned_users:
        try:
            await comment.connect(mentioned, edge=MENTIONS, mentioned_at=now)
        except Exception:
            pass
        try:
            await notification_router.dispatch(
                user_id=mentioned.user_id or mentioned.id,
                kind="mention",
                payload={
                    "comment_id": comment.id,
                    "entry_id": entry_id,
                    "entry_title": entry.title or "",
                    "actor_id": user_id,
                    "actor_name": actor_name,
                    "snippet": (text or "")[:280],
                    "track_id": entry.track_id or "",
                },
                actor_id=user_id,
                actor_kind="human",
                idempotency_key=f"mention:{comment.id}:{mentioned.id}",
            )
        except Exception:
            pass

    try:
        from app.services.notification_router import notify_entry_watchers

        await notify_entry_watchers(
            entry_id=entry_id,
            action="comment",
            actor_id=user_id,
            actor_kind="human",
            comment_text=text,
            idempotency_suffix=comment.id,
        )
    except Exception as exc:
        logger.warning(
            "Failed to trigger comment notifications for entry %s watchers: %s",
            entry_id,
            exc,
        )

    comment_export = await export_node(comment)
    await attach_author_exports([comment_export])
    return {
        "comment": comment_export,
        "message": "Comment created successfully",
    }


@endpoint("/comments/{comment_id}", methods=["PUT"], auth=True, tags=["Comments"])
async def update_comment(
    request: Request,
    comment_id: str,
    text: str,
) -> Dict[str, Any]:
    """Update a comment (author only)."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    from app.services.comment_creation import mutate_comment_record

    return await mutate_comment_record(
        user_id=user_id,
        comment_id=comment_id,
        text=text,
        request=request,
    )


@endpoint("/comments/{comment_id}", methods=["DELETE"], auth=True, tags=["Comments"])
async def delete_comment(request: Request, comment_id: str) -> Dict[str, Any]:
    """Delete one's own comment, or moderate with current track authority."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    from app.services.comment_creation import mutate_comment_record

    return await mutate_comment_record(
        user_id=user_id,
        comment_id=comment_id,
        delete=True,
        request=request,
    )
