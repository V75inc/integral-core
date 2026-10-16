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
    """Check the current primary-resource audience without releasing snapshots."""
    return (
        await prepare_event_payload(
            user_id, event, action=action, evaluate=evaluate, include_snapshots=False
        )
        is not None
    )


async def _current_connector(resource_id: str) -> Any:
    from jvspatial.core.context import get_default_context

    from app.agentive.nodes import Connector
    from app.services.authority_facts import primary_node_record

    context = get_default_context()
    row = await primary_node_record(context.database, Connector, resource_id)
    return None if row is None else await context._deserialize_entity(Connector, row)


async def event_release_workspace(event: ChangeEventEnvelope) -> str | None:
    """Own-account connectors may carry a current workspace-bound target list."""
    from app.services.event_history import event_workspace_id

    workspace_id = await event_workspace_id(event.scope)
    if (
        workspace_id is None
        and event.scope.startswith("user:")
        and str(event.resource_type).casefold() == "connector"
    ):
        from app.exceptions import AuthorizationUnavailableError
        from app.models.edges import HAS_CONNECTOR

        connector = await _current_connector(event.resource_id)
        if connector is not None:
            parents = await connector.nodes(
                edge=[HAS_CONNECTOR], direction="in", node=["Workspace"], limit=2
            )
            if not connector.workspace_id and not parents:
                return None
            if len(parents) != 1 or parents[0].id != connector.workspace_id:
                raise AuthorizationUnavailableError()
            return await event_workspace_id(f"ws:{parents[0].id}")
    return workspace_id


async def prepare_event_payload(
    user_id: str,
    event: ChangeEventEnvelope,
    *,
    action: str,
    evaluate: Evaluator,
    flat: bool = False,
    include_snapshots: bool = True,
) -> dict[str, Any] | None:
    """Authorize and project one recipient's payload in the same admission."""
    from app.services.app_operations.transaction_scope import (
        graph_transaction_available,
    )
    from app.services.event_snapshot_projection import project_event_snapshots
    from app.services.release_admission import release_admission

    native = graph_transaction_available()
    workspace_id = await event_release_workspace(event)
    if native and workspace_id is None and not event.scope.startswith("user:"):
        return None
    async with release_admission(user_id, workspace_id):
        if (
            native
            and workspace_id is not None
            and (await event_release_workspace(event) != workspace_id)
        ):
            return None
        if not await _can_release_event_current(
            user_id, event, action=action, evaluate=evaluate
        ):
            return None
        payload = event.to_wire_flat() if flat else event.to_wire()
        if include_snapshots:
            payload = await project_event_snapshots(
                user_id, event, payload, evaluate=evaluate, workspace_id=workspace_id
            )
        else:
            for key in ("before", "after", "details"):
                payload[key] = None
        return scrub_event_credentials(payload)


async def _can_release_event_current(
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
            if kind == "attachment" and scope.startswith("user:"):
                from app.services.permissions import get_user_node

                caller = await get_user_node(user_id)
                own_ids = {user_id}
                if caller is not None:
                    own_ids.update({caller.id, caller.user_id})
                owners = await node.nodes(
                    edge=[{HAS_ATTACHMENT.__name__: {"context.role": "avatar"}}],
                    direction="in",
                    node=["User"],
                    limit=2,
                )
                if (
                    caller is not None
                    and len(owners) == 1
                    and owners[0].id == caller.id
                    and node.uploaded_by in own_ids
                    and scope
                    in {f"user:{identity}" for identity in own_ids if identity}
                ):
                    return True
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
            from app.services.permissions import get_user_node

            connector = await _current_connector(resource_id)
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

            return scope in {
                f"ws:{resource_id}",
                f"workspace:{resource_id}",
            } and await can_access_workspace(user_id, resource_id) in {"owner", "admin"}
        # Other snapshots need a declared resource audience. A caller's own
        # event scope is not blanket permission for arbitrary embedded data.
        return False


def scrub_event_credentials(value: Any) -> Any:
    """Never release password or low-entropy OTP hashes through event snapshots."""
    if isinstance(value, dict):
        return {
            key: scrub_event_credentials(item)
            for key, item in value.items()
            if key
            not in {
                "password_hash",
                "email_verification",
                "reset_token",
                "credentials_delivery",
                "credentials_delivery_released",
            }
        }
    if isinstance(value, list):
        return [scrub_event_credentials(item) for item in value]
    return value
