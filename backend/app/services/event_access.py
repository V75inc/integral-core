"""Authorization at each user-facing event release boundary."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from app.middleware.permissions_cache import isolated_permissions_cache
from app.schemas.policy import Decision, Resource, Subject
from app.services.change_event_logger import ChangeEventEnvelope

logger = logging.getLogger(__name__)
Evaluator = Callable[..., Awaitable[Decision]]


async def can_release_event(
    user_id: str,
    event: ChangeEventEnvelope,
    *,
    action: str,
    evaluate: Evaluator,
) -> bool:
    """Subscription access alone cannot authorize a record's event snapshots.

    Missing/deleted records withhold snapshots until a separately governed
    tombstone audience is available. Evaluation is read-only to prevent denial
    events recursively producing more events. Every message bypasses process
    TTL state and gets its own request memo, including batch polling.
    """
    with isolated_permissions_cache():
        subject = Subject(kind="human", id=user_id)
        scope = event.scope or ""
        admitted = await evaluate(
            subject=subject,
            action=action,
            resource=Resource(kind="change_event", id=event.id or "", scope=scope),
            _dry_run=True,
        )
        if not admitted.allowed:
            return False
        if (
            event.action == "policy.deny"
            and event.before is None
            and event.after is None
        ):
            # Denied creates may have no resulting resource. Their forensic
            # metadata is an administrative audience, never a read audience.
            from app.services.permissions import get_user_node, resolve_role

            scope_kind, separator, scope_id = scope.partition(":")
            if separator and scope_kind in {"track", "app"}:
                return await resolve_role(user_id, scope_kind, scope_id) in {
                    "owner",
                    "admin",
                }
            caller = await get_user_node(user_id)
            own_ids = {user_id}
            if caller is not None:
                own_ids.update({caller.id, caller.user_id})
            return bool(
                scope_kind == "user"
                and scope_id in own_ids
                and event.actor_kind == "human"
                and event.actor_id in own_ids
            )
        kind = str(event.resource_type or "").strip().casefold()
        kind = "app" if kind == "space" else kind
        resource_id = event.resource_id or ""
        if kind == "policy":
            from app.models.nodes import Policy
            from app.services.permissions import get_user_node

            policy = await Policy.get(resource_id)
            caller = await get_user_node(user_id)
            own_ids = {user_id}
            if caller is not None:
                own_ids.update({caller.id, caller.user_id})
            return bool(
                policy is not None
                and policy.created_by in own_ids
                and event.actor_kind == "human"
                and event.actor_id in own_ids
                and scope in {f"user:{identity}" for identity in own_ids if identity}
            )
        if kind in {"operationalmodel", "operational_model"}:
            from app.models.edges import HAS_OPERATIONAL_MODEL
            from app.models.nodes import App, OperationalModel
            from app.services.permissions import resolve_role

            model = await OperationalModel.get(resource_id)
            if model is None:
                return False
            parents = await model.nodes(
                edge=[HAS_OPERATIONAL_MODEL],
                direction="in",
                node=["Track", "WorkspaceApp"],
                limit=2,
            )
            if len(parents) != 1:
                return False
            parent = parents[0]
            parent_kind = "app" if isinstance(parent, App) else "track"
            return bool(
                scope == f"{parent_kind}:{parent.id}"
                and await resolve_role(user_id, parent_kind, parent.id)
                in {"owner", "admin"}
            )
        if kind in {"entry", "track", "app", "view"}:
            result = await evaluate(
                subject=subject,
                action=f"{kind}.read",
                resource=Resource(kind=kind, id=resource_id, scope=scope),
                _dry_run=True,
            )
            return result.allowed
        if kind in {"comment", "attachment"}:
            from app.models.edges import HAS_ATTACHMENT, HAS_COMMENT
            from app.models.nodes import Attachment, Comment

            node = await (Comment if kind == "comment" else Attachment).get(resource_id)
            if node is None:
                return False
            parents = await node.nodes(
                edge=[HAS_COMMENT if kind == "comment" else HAS_ATTACHMENT],
                direction="in",
                node=["Entry"],
                limit=2,
            )
            if len(parents) != 1:
                return False
            result = await evaluate(
                subject=subject,
                action="entry.read",
                resource=Resource(kind="entry", id=parents[0].id, scope=scope),
                _dry_run=True,
            )
            return result.allowed
        if kind == "user":
            from app.services.permissions import get_user_node

            caller = await get_user_node(user_id)
            own_ids = {user_id}
            if caller is not None:
                own_ids.update({caller.id, caller.user_id})
            return resource_id in own_ids and scope.startswith("user:")
        if kind == "connector":
            from app.agentive.nodes import Connector
            from app.services.permissions import get_user_node

            connector = await Connector.get(resource_id)
            caller = await get_user_node(user_id)
            own_ids = {user_id}
            if caller is not None:
                own_ids.update({caller.id, caller.user_id})
            if connector is not None:
                return connector.owner in own_ids
            # Personal Connector deletion history has a server-recorded owner,
            # unlike a mutable resource ACL. Legacy snapshots without that
            # proof stay withheld. A user scope alone cannot confer ownership.
            return bool(
                event.action == "connector.delete"
                and (event.before or {}).get("owner") in own_ids
                and event.actor_id in own_ids
                and scope in {f"user:{identity}" for identity in own_ids if identity}
            )
        if kind == "workspace":
            from app.services.workspace_permissions import can_access_workspace

            return await can_access_workspace(user_id, resource_id) != "none"
        # Other snapshots need a declared resource audience. A caller's own
        # event scope is not blanket permission for arbitrary embedded data.
        return False


def scrub_event_credentials(value: Any) -> Any:
    """Never release password or low-entropy OTP hashes through event snapshots."""
    if isinstance(value, dict):
        return {
            key: scrub_event_credentials(item)
            for key, item in value.items()
            if key not in {"password_hash", "email_verification", "reset_token"}
        }
    if isinstance(value, list):
        return [scrub_event_credentials(item) for item in value]
    return value
