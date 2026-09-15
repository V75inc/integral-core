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
from app.models.edges import AUTHORED_BY, HAS_COMMENT, MENTIONS
from app.models.nodes import Comment, Entry
from app.schemas.policy import Resource, Subject
from app.services import notification_router
from app.services.change_event import emit_change_event
from app.services.content_moderation import validate_no_profanity
from app.services.mentions import resolve_mentions
from app.services.permissions import get_user_node
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

    entry = await Entry.get(entry_id)
    if not entry:
        raise ResourceNotFoundError(message="Entry not found")

    comment_decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="comment.create",
        resource=Resource(
            kind="entry",
            id=entry_id,
            scope=f"track:{entry.track_id or ''}",
        ),
    )
    if not comment_decision.allowed:
        raise InsufficientPermissionsError(
            message="You need commenter access or higher to post comments."
        )

    parent_comment = None
    if parent_id:
        parent_comment = await Comment.get(parent_id)
        if not parent_comment:
            raise ResourceNotFoundError(message="Parent comment not found")

    validate_no_profanity(text, "comment")

    now = utc_now_iso()
    comment = await Comment.create(
        author_id=user_id,
        parent_id=parent_id,
        text=text,
        created_at=now,
        updated_at=now,
    )

    if parent_id and parent_comment:
        await parent_comment.connect(comment, edge=HAS_COMMENT, created_at=now)
    await entry.connect(comment, edge=HAS_COMMENT, created_at=now)

    user = await get_user_node(user_id)
    if user:
        await comment.connect(user, edge=AUTHORED_BY, authored_at=now)

    # Enrich event snapshot with parent-entry context so the activity feed
    # can render "X commented on '<entry title>'" without an extra round-trip.
    comment_snapshot = await export_node(comment)
    comment_snapshot["_entry_id"] = entry_id
    comment_snapshot["_entry_title"] = entry.title or ""
    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="comment.create",
        resource_type="Comment",
        resource_id=comment.id,
        before=None,
        after=comment_snapshot,
        scope=f"track:{entry.track_id or ''}",
    )

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
    comment = await Comment.get(comment_id)
    if not comment:
        raise ResourceNotFoundError(message="Comment not found")
    if comment.author_id != user_id:
        raise InsufficientPermissionsError(
            message="You can only update your own comments"
        )

    parent_entries = await comment.nodes(
        edge=["HAS_COMMENT"], direction="in", node=["Entry"]
    )
    if not parent_entries:
        raise ResourceNotFoundError(message="Parent entry not found")
    parent_entry = parent_entries[0]
    read_decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="entry.read",
        resource=Resource(
            kind="entry",
            id=parent_entry.id,
            scope=f"track:{parent_entry.track_id or ''}",
        ),
    )
    if not read_decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")

    validate_no_profanity(text, "comment")

    prior_snapshot = await export_node(comment)  # D-03 before-snapshot
    comment.text = text
    comment.updated_at = utc_now_iso()
    await comment.save()

    scope_track_id = parent_entry.track_id or ""
    parent_entry_id = parent_entry.id
    parent_entry_title = parent_entry.title or ""

    after_snapshot = await export_node(comment)
    after_snapshot["_entry_id"] = parent_entry_id
    after_snapshot["_entry_title"] = parent_entry_title
    prior_snapshot["_entry_id"] = parent_entry_id
    prior_snapshot["_entry_title"] = parent_entry_title

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="comment.update",
        resource_type="Comment",
        resource_id=comment.id,
        before=prior_snapshot,
        after=after_snapshot,
        scope=f"track:{scope_track_id}",
    )

    comment_export = await export_node(comment)
    await attach_author_exports([comment_export])
    return {
        "comment": comment_export,
        "message": "Comment updated successfully",
    }


@endpoint("/comments/{comment_id}", methods=["DELETE"], auth=True, tags=["Comments"])
async def delete_comment(
    request: Request,
    comment_id: str,
) -> Dict[str, Any]:
    """Delete a comment — its author, or an admin/owner moderating the track."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    comment = await Comment.get(comment_id)
    if not comment:
        raise ResourceNotFoundError(message="Comment not found")

    parent_entries = await comment.nodes(
        edge=["HAS_COMMENT"], direction="in", node=["Entry"]
    )
    if not parent_entries:
        raise ResourceNotFoundError(message="Parent entry not found")
    parent_entry = parent_entries[0]

    # Author-or-moderator. Author-only left public-share comments undeletable
    # by ANYONE: the public endpoint stamps a literal ``author_id="public"``
    # (tracks_public_share.create_public_track_entry_comment), which equals no
    # principal id, so enabling public comments was a one-way door — a visitor
    # could post and the track owner could not remove it.
    #
    # ``comment.moderate`` is admin-tier in policy_engine, deliberately NOT the
    # commenter tier the other comment.* actions ride: this authorizes deleting
    # SOMEONE ELSE's words. Editing stays author-only (update_comment) —
    # rewriting another person's comment is not the same act as removing it.
    is_moderation = comment.author_id != user_id
    if is_moderation:
        moderation_decision = await policy_evaluate(
            subject=Subject(kind="human", id=user_id),
            action="comment.moderate",
            resource=Resource(
                kind="comment",
                id=comment_id,
                scope=f"track:{parent_entry.track_id or ''}",
            ),
        )
        if not moderation_decision.allowed:
            raise InsufficientPermissionsError(
                message=(
                    "You can only delete your own comments, unless you "
                    "administer this track"
                )
            )

    read_decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="entry.read",
        resource=Resource(
            kind="entry",
            id=parent_entry.id,
            scope=f"track:{parent_entry.track_id or ''}",
        ),
    )
    if not read_decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")

    parent_entries = await comment.nodes(
        edge=["HAS_COMMENT"], direction="in", node=["Entry"]
    )
    if not parent_entries:
        raise ResourceNotFoundError(message="Parent entry not found")
    parent_entry = parent_entries[0]
    read_decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="entry.read",
        resource=Resource(
            kind="entry",
            id=parent_entry.id,
            scope=f"track:{parent_entry.track_id or ''}",
        ),
    )
    if not read_decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")

    prior_snapshot = await export_node(comment)  # D-03 before-snapshot

    # Resolve scope + parent-entry title BEFORE deletion (the edge disappears
    # with the comment node).
    scope_track_id = ""
    parent_entry_id = ""
    parent_entry_title = ""
    try:
        parent_entries = await comment.nodes(
            edge=["HAS_COMMENT"], direction="in", node=["Entry"]
        )
        if parent_entries:
            scope_track_id = parent_entries[0].track_id or ""
            parent_entry_id = parent_entries[0].id
            parent_entry_title = parent_entries[0].title or ""
    except Exception:
        pass

    prior_snapshot["_entry_id"] = parent_entry_id
    prior_snapshot["_entry_title"] = parent_entry_title
    # An admin removing somebody else's comment and an author removing their
    # own are the same action verb with very different meaning; without this
    # the audit trail cannot tell them apart.
    prior_snapshot["_moderated"] = is_moderation

    await comment.delete()

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="comment.delete",
        resource_type="Comment",
        resource_id=comment_id,
        before=prior_snapshot,
        after=None,
        scope=f"track:{scope_track_id}",
    )

    return {"message": "Comment deleted successfully", "deleted_comment_id": comment_id}
