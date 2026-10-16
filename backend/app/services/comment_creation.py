"""Canonical identified comment creation and exact staged effect receipts."""

from dataclasses import dataclass
from typing import Any

from jvspatial.core.context import get_default_context

from app.api.errors import InsufficientPermissionsError, ResourceNotFoundError
from app.api.utils import export_node
from app.models.edges import AUTHORED_BY, HAS_COMMENT
from app.models.nodes import Comment, Entry, User
from app.schemas.policy import Resource, Subject
from app.services.authority_facts import primary_node_record
from app.services.resource_location import DATA_APP_STATES, locate_resource


@dataclass(frozen=True)
class CreatedComment:
    comment: Comment
    entry: Entry
    user: User


async def comment_parent_entry(comment: Comment) -> Entry:
    """Require one live direct Entry parent, never select a first arbitrary parent."""
    parents = await comment.nodes(
        edge=[HAS_COMMENT], direction="in", node=["Entry"], limit=2
    )
    if len(parents) != 1:
        raise InsufficientPermissionsError(message="Comment parent is unavailable")
    return parents[0]


async def create_comment_record(
    *,
    user_id: str,
    entry_id: str,
    text: str,
    parent_id: str | None = None,
    request: Any = None,
) -> CreatedComment:
    """Commit the graph, event outbox and approved creation receipt together."""
    from app.agentive.services.staging_execution import (
        deliver_staging_command_events,
        staged_command_effect,
    )
    from app.services.app_operations.transaction_scope import (
        graph_transaction_available,
    )
    from app.services.change_event import emit_change_event
    from app.services.content_moderation import validate_no_profanity
    from app.services.membership_write_scope import membership_write_scope
    from app.services.permissions import get_user_node
    from app.services.policy_engine import evaluate
    from app.services.request_scope import resolve_create_workspace_id
    from app.services.resource_events import queue_resource_event
    from app.services.workspace_membership import _deliver
    from app.utils.time import utc_now_iso

    location = await locate_resource(
        "entry", entry_id, allowed_app_states=DATA_APP_STATES
    )
    if location is None:
        raise ResourceNotFoundError(message="Entry not found")
    outbox = None
    event = None
    native = graph_transaction_available()
    async with membership_write_scope(location.workspace_id, principal_ids=(user_id,)):
        current = await locate_resource(
            "entry", entry_id, allowed_app_states=DATA_APP_STATES
        )
        if current != location:
            raise InsufficientPermissionsError(message="Comment target changed")
        await resolve_create_workspace_id(request, user_id, current.workspace_id)
        context = get_default_context()
        row = await primary_node_record(context.database, Entry, entry_id)
        if row is None:
            raise ResourceNotFoundError(message="Entry not found")
        entry = await context._deserialize_entity(Entry, row)
        user = await get_user_node(user_id)
        if user is None:
            raise InsufficientPermissionsError(message="Current author is unavailable")
        decision = await evaluate(
            subject=Subject(kind="human", id=user_id),
            action="comment.create",
            resource=Resource(
                kind="entry", id=entry_id, scope=f"track:{current.track_id}"
            ),
        )
        if not decision.allowed:
            raise InsufficientPermissionsError(
                message="You need commenter access or higher to post comments."
            )
        parent = None
        if parent_id:
            parent_row = await primary_node_record(context.database, Comment, parent_id)
            if native:
                parent_row = await context.database.find_one_and_update(
                    "node",
                    {"id": parent_id, "entity": Comment._entity_name()},
                    {"$set": {}},
                )
            if parent_row is None:
                raise ResourceNotFoundError(message="Parent comment not found")
            parent = await context._deserialize_entity(Comment, parent_row)
            if (await comment_parent_entry(parent)).id != entry_id:
                raise ResourceNotFoundError(message="Parent comment not found")
        validate_no_profanity(text, "comment")
        async with staged_command_effect(
            user_id=user_id,
            workspace_id=current.workspace_id,
            kind="add_comment",
            targets={"entry_id": entry_id, "body": text, "parent_id": parent_id},
        ) as complete:
            now = utc_now_iso()
            comment = await Comment.create(
                author_id=user_id,
                parent_id=parent_id,
                text=text,
                created_at=now,
                updated_at=now,
            )
            if parent is not None:
                await parent.connect(comment, edge=HAS_COMMENT, created_at=now)
            await entry.connect(comment, edge=HAS_COMMENT, created_at=now)
            await comment.connect(user, edge=AUTHORED_BY, authored_at=now)
            snapshot = await export_node(comment)
            snapshot.update({"_entry_id": entry_id, "_entry_title": entry.title or ""})
            event = {
                "actor_kind": "human",
                "actor_id": user_id,
                "action": "comment.create",
                "resource_type": "Comment",
                "resource_id": comment.id,
                "before": None,
                "after": snapshot,
                "scope": f"track:{current.track_id}",
            }
            outbox = await queue_resource_event(
                event, current.workspace_id, native, namespace="comment-creation-v1"
            )
            if complete is not None:
                await complete({"comment_id": comment.id, "entry_id": entry_id})
    # No network or notification callback runs inside graph/authority locks.
    if outbox:
        await _deliver(outbox)
    await deliver_staging_command_events()
    if not native and event is not None:
        await emit_change_event(**event)
    return CreatedComment(comment=comment, entry=entry, user=user)


async def mutate_comment_record(
    *,
    user_id: str,
    comment_id: str,
    text: str | None = None,
    delete: bool = False,
    request: Any = None,
) -> dict[str, Any]:
    """Apply an author edit or authorized removal with an atomic receipt."""
    from app.agentive.services.staging_execution import (
        deliver_staging_command_events,
        staged_command_effect,
    )
    from app.api.utils import attach_author_exports
    from app.services.app_operations.transaction_scope import (
        graph_transaction_available,
    )
    from app.services.change_event import emit_change_event
    from app.services.content_moderation import validate_no_profanity
    from app.services.membership_write_scope import membership_write_scope
    from app.services.permissions import get_user_node
    from app.services.policy_engine import evaluate
    from app.services.request_scope import require_effect_target_scope
    from app.services.resource_events import queue_resource_event
    from app.services.workspace_membership import _deliver
    from app.utils.time import utc_now_iso

    context = get_default_context()
    row = await primary_node_record(context.database, Comment, comment_id)
    if row is None:
        raise ResourceNotFoundError(message="Comment not found")
    comment = await context._deserialize_entity(Comment, row)
    entry = await comment_parent_entry(comment)
    location = await locate_resource(
        "entry", entry.id, allowed_app_states=DATA_APP_STATES
    )
    if location is None:
        raise ResourceNotFoundError(message="Parent entry not found")
    native = graph_transaction_available()
    kind = "delete_comment" if delete else "edit_comment"
    async with membership_write_scope(location.workspace_id, principal_ids=(user_id,)):
        # Rebind to the transaction context before hydrating or writing nodes.
        context = get_default_context()
        row = await primary_node_record(context.database, Comment, comment_id)
        if native:
            row = await context.database.find_one_and_update(
                "node",
                {"id": comment_id, "entity": Comment._entity_name()},
                {"$set": {}},
            )
        if row is None:
            raise ResourceNotFoundError(message="Comment not found")
        comment = await context._deserialize_entity(Comment, row)
        parent = await comment_parent_entry(comment)
        current = await locate_resource(
            "entry", parent.id, allowed_app_states=DATA_APP_STATES
        )
        if parent.id != entry.id or current != location:
            raise InsufficientPermissionsError(message="Comment target changed")
        await require_effect_target_scope(request, user_id, current.workspace_id)
        user = await get_user_node(user_id)
        if user is None:
            raise InsufficientPermissionsError(message="Access denied")
        # Both supported identity forms resolve to this same current principal.
        own = comment.author_id in {user_id, user.id, user.user_id}
        if not own:
            if not delete:
                raise InsufficientPermissionsError(
                    message="You can only update your own comments"
                )
            decision = await evaluate(
                subject=Subject(kind="human", id=user_id),
                action="comment.moderate",
                resource=Resource(
                    kind="comment", id=comment_id, scope=f"track:{current.track_id}"
                ),
            )
            if not decision.allowed:
                raise InsufficientPermissionsError(
                    message="You can only delete your own comments, unless you administer this track"
                )
        decision = await evaluate(
            subject=Subject(kind="human", id=user_id),
            action="entry.read",
            resource=Resource(
                kind="entry", id=entry.id, scope=f"track:{current.track_id}"
            ),
        )
        if not decision.allowed:
            raise InsufficientPermissionsError(message="Access denied")
        targets = {"comment_id": comment_id}
        if not delete:
            validate_no_profanity(text or "", "comment")
            targets["text"] = text or ""
        async with staged_command_effect(
            user_id=user_id,
            workspace_id=current.workspace_id,
            kind=kind,
            targets=targets,
        ) as complete:
            before = await export_node(comment)
            before.update({"_entry_id": entry.id, "_entry_title": entry.title or ""})
            if delete:
                before["_moderated"] = not own
                await comment.delete()
                after = None
            else:
                comment.text = text or ""
                comment.updated_at = utc_now_iso()
                # The row is already locked and its class is known. Use the
                # public context save to avoid lazy index DDL under a row lock.
                await context.save(comment)
                after = await export_node(comment)
                after.update({"_entry_id": entry.id, "_entry_title": entry.title or ""})
            event = {
                "actor_kind": "human",
                "actor_id": user_id,
                "action": "comment.delete" if delete else "comment.update",
                "resource_type": "Comment",
                "resource_id": comment_id,
                "before": before,
                "after": after,
                "scope": f"track:{current.track_id}",
            }
            outbox = await queue_resource_event(
                event,
                current.workspace_id,
                native,
                namespace="comment-mutation-v1",
            )
            if complete is not None:
                await complete(
                    {"comment_id": comment_id, "entry_id": entry.id, "deleted": delete}
                )
    if outbox:
        await _deliver(outbox)
    await deliver_staging_command_events()
    if not native:
        await emit_change_event(**event)
    if delete:
        return {
            "message": "Comment deleted successfully",
            "deleted_comment_id": comment_id,
        }
    exported = await export_node(comment)
    await attach_author_exports([exported])
    return {"comment": exported, "message": "Comment updated successfully"}
