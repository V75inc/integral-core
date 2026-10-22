"""Authorized reaction selection using typed associative edges.

Legacy Entry.reactions remains a compatibility projection. Only the actor's
legacy slots are reconciled on their first write; other historical data is
preserved, including multiple emoji for the same actor until they select.
"""

from typing import Any, Literal

from jvspatial.core.context import get_default_context

from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    ResourceNotFoundError,
)
from app.models.edges import REACTS_TO
from app.models.nodes import Comment, Entry
from app.schemas.policy import Resource, Subject
from app.services.authority_facts import primary_node_record
from app.services.resource_location import DATA_APP_STATES, locate_resource

REACTIONS = ("👍", "👎", "❤️", "😊", "😢")


async def reaction_summaries(targets: list[Any]) -> dict[str, dict[str, list[str]]]:
    """Project one authorized list window with one reaction-edge query."""
    results = {
        target.id: {
            emoji: list(dict.fromkeys(users))
            for emoji, users in (getattr(target, "reactions", {}) or {}).items()
            if users
        }
        for target in targets
    }
    if not results:
        return results
    # Export may run after the target's creation transaction has committed.
    # Use the task's current binding, never a retained node transaction.
    context = get_default_context()
    edges = await context.find(REACTS_TO, {"target": {"$in": list(results)}})
    for edge in sorted(edges, key=lambda item: (item.updated_at or "", item.id)):
        actor = edge.principal_id
        result = results[edge.target]
        if not actor:
            continue
        for emoji in list(result):
            result[emoji] = [user for user in result[emoji] if user != actor]
            if not result[emoji]:
                del result[emoji]
        result.setdefault(edge.emoji, []).append(actor)
    return results


async def reaction_summary(target: Any) -> dict[str, list[str]]:
    """Merge untouched legacy reactions with the authoritative edge projection."""
    return (await reaction_summaries([target]))[target.id]


async def mutate_reaction(
    *,
    user_id: str,
    target_id: str,
    emoji: str,
    target_kind: Literal["entry", "comment"] = "entry",
    mode: Literal["select", "toggle", "remove"] = "select",
    request: Any = None,
) -> dict[str, Any]:
    """Serialize permission checks, migration, edge mutation and event outbox.

    The existing workspace membership fence uses PostgreSQL authority row
    locking in a graph transaction, and a process lock on local stores. It
    serializes concurrent first reactions without relying on check/connect.
    """
    from app.api.utils import export_node
    from app.services.app_operations.transaction_scope import (
        graph_transaction_available,
    )
    from app.services.change_event import emit_change_event
    from app.services.comment_creation import comment_parent_entry
    from app.services.membership_write_scope import membership_write_scope
    from app.services.permissions import get_user_node
    from app.services.policy_engine import evaluate
    from app.services.request_scope import require_effect_target_scope
    from app.services.resource_events import queue_resource_event
    from app.services.workspace_membership import _deliver
    from app.utils.time import utc_now_iso

    if mode == "select" and emoji not in REACTIONS:
        raise BadRequestError(
            message="Choose thumbs up, thumbs down, love, happy, or sad"
        )
    if not emoji or len(emoji) > 32:
        raise BadRequestError(message="Invalid reaction")
    cls = Comment if target_kind == "comment" else Entry
    target = await cls.get(target_id)
    if target is None:
        raise ResourceNotFoundError(message="Reaction target not found")
    entry = await comment_parent_entry(target) if target_kind == "comment" else target
    location = await locate_resource(
        "entry", entry.id, allowed_app_states=DATA_APP_STATES
    )
    if location is None:
        raise ResourceNotFoundError(message="Entry not found")
    native = graph_transaction_available()
    async with membership_write_scope(location.workspace_id, principal_ids=(user_id,)):
        context = get_default_context()
        row = await primary_node_record(context.database, cls, target_id)
        if native:
            row = await context.database.find_one_and_update(
                "node", {"id": target_id, "entity": cls._entity_name()}, {"$set": {}}
            )
        if row is None:
            raise ResourceNotFoundError(message="Reaction target not found")
        target = await context._deserialize_entity(cls, row)
        entry = (
            await comment_parent_entry(target) if target_kind == "comment" else target
        )
        current = await locate_resource(
            "entry", entry.id, allowed_app_states=DATA_APP_STATES
        )
        if current != location:
            raise InsufficientPermissionsError(message="Reaction target changed")
        await require_effect_target_scope(request, user_id, location.workspace_id)
        user = await get_user_node(user_id)
        if user is None:
            raise InsufficientPermissionsError(message="Current principal unavailable")
        aliases = {user_id, user.id, user.user_id} - {"", None}
        summary = await reaction_summary(target)
        present = bool(aliases.intersection(summary.get(emoji, [])))
        retract = mode == "remove" or (mode == "toggle" and present)
        decision = await evaluate(
            subject=Subject(kind="human", id=user_id),
            action="entry.read" if retract else "reaction.create",
            resource=Resource(
                kind="entry", id=entry.id, scope=f"track:{location.track_id}"
            ),
        )
        if not decision.allowed:
            raise InsufficientPermissionsError(
                message=(
                    "Access denied"
                    if retract
                    else "You need commenter access or higher to react."
                )
            )
        before = await export_node(target)
        edges = await context.find_edges_between(
            user.id, target.id, edge_class=REACTS_TO
        )
        for edge in edges:
            if not retract or edge.emoji == emoji:
                await edge.delete()
        legacy = getattr(target, "reactions", None)
        if legacy is not None:
            for old in list(legacy):
                if not retract or old == emoji:
                    legacy[old] = [
                        actor for actor in legacy[old] if actor not in aliases
                    ]
                    if not legacy[old]:
                        del legacy[old]
        if not retract:
            await user.connect(
                target,
                edge=REACTS_TO,
                emoji=emoji,
                principal_id=user_id,
                updated_at=utc_now_iso(),
            )
            if legacy is not None:
                legacy.setdefault(emoji, []).append(user_id)
        if legacy is not None:
            await context.save(target)
        after = await export_node(target)
        if target_kind == "comment":
            before.update({"_entry_id": entry.id, "_entry_title": entry.title or ""})
            after.update({"_entry_id": entry.id, "_entry_title": entry.title or ""})
        event = {
            "actor_kind": "human",
            "actor_id": user_id,
            "action": f"{target_kind}.update",
            "resource_type": cls.__name__,
            "resource_id": target.id,
            "before": before,
            "after": after,
            "scope": f"track:{location.track_id}",
        }
        outbox = await queue_resource_event(
            event, location.workspace_id, native, namespace="reaction-selection-v1"
        )
        result = {
            "reactions": after["reactions"],
            f"{target_kind}_id": target.id,
            "message": "Reaction updated",
        }
    if outbox:
        await _deliver(outbox)
    if not native:
        await emit_change_event(**event)
    return result
