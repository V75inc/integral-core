"""Exact approved top-level comment creation with current identified authority."""

from typing import Any

from app.api.errors import BadRequestError, InsufficientPermissionsError
from app.schemas.policy import Resource, Subject


async def create_comment_internal(
    *,
    actor_kind: str,
    actor_id: str,
    payload: dict[str, Any],
    _internal_actor: Subject | None = None,
) -> dict:
    """Bind target, body and current human author before graph effects."""
    from app.models.edges import AUTHORED_BY, HAS_COMMENT
    from app.models.nodes import Comment, Entry
    from app.services.approved_effect import (
        approved_effect_principal,
        capture_approved_change_event,
    )
    from app.services.change_event import emit_change_event
    from app.services.membership_write_scope import membership_write_scope
    from app.services.permissions import get_user_node
    from app.services.policy_engine import evaluate
    from app.services.resource_location import locate_resource
    from app.utils.time import utc_now_iso

    if (
        set(payload) - {"entry_id", "body"}
        or not isinstance(payload.get("entry_id"), str)
        or not payload["entry_id"]
        or not isinstance(payload.get("body"), str)
        or not payload["body"].strip()
    ):
        raise BadRequestError(message="Invalid approved comment")
    location = await locate_resource("entry", payload["entry_id"])
    if location is None:
        raise InsufficientPermissionsError(message="Comment target unavailable")
    principal = await approved_effect_principal(
        actor_kind=actor_kind,
        actor_id=actor_id,
        action="comment.create",
        resource_type="entry",
        resource_id=payload["entry_id"],
        payload=payload,
    )
    if principal is None:
        if actor_kind != "human":
            raise InsufficientPermissionsError(
                message="Exact approved authority required"
            )
        user = await get_user_node(actor_id)
        principal = user.id if user else ""
    async with membership_write_scope(
        location.workspace_id, principal_ids=(principal,)
    ):
        current = await locate_resource("entry", payload["entry_id"])
        if current != location:
            raise InsufficientPermissionsError(message="Comment target changed")
        decision = await evaluate(
            subject=Subject(kind=actor_kind, id=actor_id),
            action="comment.create",
            resource=Resource(
                kind="entry", id=payload["entry_id"], scope=f"track:{location.track_id}"
            ),
            payload=payload,
        )
        if not decision.allowed:
            raise InsufficientPermissionsError(message="Comment creation denied")
        user = await get_user_node(principal)
        entry = await Entry.find_one({"id": payload["entry_id"]})
        if user is None or entry is None:
            raise InsufficientPermissionsError(message="Comment target unavailable")
        now = utc_now_iso()
        comment = await Comment.create(
            body=payload["body"],
            author_id=user.id,
            entry_id=entry.id,
            created_at=now,
            updated_at=now,
        )
        await entry.connect(comment, edge=HAS_COMMENT, posted_at=now)
        await comment.connect(user, edge=AUTHORED_BY, authored_at=now)
        event = {
            "actor_kind": actor_kind,
            "actor_id": actor_id,
            "action": "comment.create",
            "resource_type": "Comment",
            "resource_id": comment.id,
            "before": None,
            "after": await comment.export(flat=True),
            "scope": f"track:{location.track_id}",
        }
        if not await capture_approved_change_event(event):
            await emit_change_event(**event)
        return await comment.export(flat=True)
