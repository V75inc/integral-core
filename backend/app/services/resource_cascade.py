"""Exact owner-governed cascade authority for one canonical subtree."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import AsyncIterator

from jvspatial.core.context import get_default_context

from app.exceptions import AuthorizationUnavailableError
from app.middleware.permissions_cache import isolated_permissions_cache
from app.models.nodes import Entry
from app.schemas.policy import Resource, Subject
from app.services.authorization_scope import authorization_scope
from app.services.entry_write_scope import (
    entry_write_scope,
    is_postgres_entry_transaction,
)
from app.services.identity_admission import identity_admission
from app.services.permissions import get_user_node
from app.services.resource_location import KNOWN_APP_STATES, locate_resource


@dataclass(frozen=True)
class CascadePermit:
    actor_id: str
    workspace_id: str
    resource_type: str
    resource_id: str
    node_ids: frozenset[str]
    entry_ids: frozenset[str]
    task: object
    database: object


_active: ContextVar[CascadePermit | None] = ContextVar(
    "integral_cascade_permit", default=None
)


@asynccontextmanager
async def resource_cascade_scope(
    *, actor_id: str, resource_type: str, resource_id: str, workspace_id: str
) -> AsyncIterator[None]:
    """Admit a named destructive action; children cannot escape its captured roots."""
    from app.api.errors import InsufficientPermissionsError
    from app.services.policy_engine import evaluate
    from app.services.workspace_rehome import RehomeInventory

    actor = await get_user_node(actor_id) if actor_id else None
    if actor is None or resource_type not in {"workspace", "app", "track"}:
        raise InsufficientPermissionsError(
            message="Explicit current cascade actor required"
        )
    parent = _active.get()
    if parent is not None:
        if (
            parent.task is not asyncio.current_task()
            or parent.database is not get_default_context().database
            or parent.actor_id != actor.id
            or parent.workspace_id != workspace_id
            or resource_id not in parent.node_ids
        ):
            raise AuthorizationUnavailableError()
        current = await locate_resource(
            resource_type, resource_id, allowed_app_states=KNOWN_APP_STATES
        )
        if current is None or current.workspace_id != workspace_id:
            raise AuthorizationUnavailableError()
        yield
        return
    with isolated_permissions_cache():
        async with (
            entry_write_scope(f"cascade:{workspace_id}"),
            identity_admission((actor.id,)),
        ):
            database = get_default_context().database
            async with AsyncExitStack() as stack:
                if is_postgres_entry_transaction(database):
                    await stack.enter_async_context(
                        authorization_scope(workspace_id, authority_mutation=True)
                    )
                current = await locate_resource(
                    resource_type, resource_id, allowed_app_states=KNOWN_APP_STATES
                )
                if current is None or current.workspace_id != workspace_id:
                    raise AuthorizationUnavailableError()
                if resource_type == "workspace":
                    from app.services.workspace_governance import (
                        owner_governance_allowed,
                    )
                    from app.services.workspace_permissions import can_access_workspace

                    allowed = (
                        await can_access_workspace(actor.id, workspace_id) == "owner"
                        and owner_governance_allowed()
                    )
                else:
                    decision = await evaluate(
                        subject=Subject(kind="human", id=actor.id),
                        action=f"{resource_type}.delete",
                        resource=Resource(
                            kind=resource_type,
                            id=resource_id,
                            scope=f"{resource_type}:{resource_id}",
                        ),
                    )
                    allowed = decision.allowed
                if not allowed:
                    raise InsufficientPermissionsError(
                        message="Current subtree deletion authority required"
                    )
                from app.services.permissions import _load_resource_node

                root = await _load_resource_node(resource_type, resource_id)
                if root is None:
                    raise AuthorizationUnavailableError()
                # Rehome inventory includes every linked Track's records. An
                # App delete instead retains shared Tracks and only unlinks
                # its own edge, so those branches must never acquire a
                # destructive permit (or require Entry deletion authority).
                from jvspatial.core import Node
                from jvspatial.core.decorators import on_visit

                from app.models.nodes import Track
                from app.services.app_deletion import _apps_containing_track

                class CascadeInventory(RehomeInventory):
                    @on_visit(Node)
                    async def collect(self, here: Node) -> None:
                        if resource_type == "app" and isinstance(here, Track):
                            parents = await _apps_containing_track(here)
                            parent_ids = {parent.id for parent in parents}
                            if resource_id not in parent_ids:
                                raise AuthorizationUnavailableError()
                            if parent_ids != {resource_id}:
                                return
                        await super().collect(here)

                walker = CascadeInventory()
                await walker.spawn(root)
                report = await walker.get_report()
                if any(
                    isinstance(item, dict) and item.get("hook_error") for item in report
                ):
                    raise AuthorizationUnavailableError()
                entries = set()
                for node in walker.resources.values():
                    if not isinstance(node, Entry):
                        continue
                    location = await locate_resource(
                        "entry", node.id, allowed_app_states=KNOWN_APP_STATES
                    )
                    if (
                        location is None
                        or location.workspace_id != workspace_id
                        or (resource_type == "app" and location.app_id != resource_id)
                        or (
                            resource_type == "track"
                            and location.track_id != resource_id
                        )
                    ):
                        raise AuthorizationUnavailableError()
                    entries.add(node.id)
                permit = CascadePermit(
                    actor.id,
                    workspace_id,
                    resource_type,
                    resource_id,
                    frozenset(walker.resources),
                    frozenset(entries),
                    asyncio.current_task(),
                    database,
                )
                token = _active.set(permit)
                try:
                    yield
                finally:
                    _active.reset(token)


def cascade_manages_tracks() -> bool:
    """App/workspace commands already own their complete Track deletion order."""
    permit = _active.get()
    return bool(
        permit is not None
        and permit.task is asyncio.current_task()
        and permit.database is get_default_context().database
        and permit.resource_type in {"app", "workspace"}
    )


async def entry_cascade_permitted(actor_id: str, entry_id: str) -> bool:
    """Authorize only an exact still-contained child in the admitted owner unit."""
    permit = _active.get()
    if (
        permit is None
        or permit.task is not asyncio.current_task()
        or permit.database is not get_default_context().database
        or entry_id not in permit.entry_ids
    ):
        return False
    actor = await get_user_node(actor_id) if actor_id else None
    if actor is None or actor.id != permit.actor_id:
        return False
    location = await locate_resource(
        "entry", entry_id, allowed_app_states=KNOWN_APP_STATES
    )
    return bool(
        location is not None
        and location.workspace_id == permit.workspace_id
        and (permit.resource_type != "app" or location.app_id == permit.resource_id)
        and (permit.resource_type != "track" or location.track_id == permit.resource_id)
    )
