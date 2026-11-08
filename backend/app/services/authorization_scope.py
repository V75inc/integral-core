"""Shared-store authorization ordering seam for workspace-bound effects.

PostgreSQL holds the logical workspace lock until graph/receipt commit. Callers
must re-evaluate current authority *inside* this scope, then perform the
protected effect before leaving. Authority mutations bump the revision in
that same transaction. This is infrastructure, not an implicit grant.

Stores without graph transactions deliberately refuse this strong seam;
callers cannot silently turn it into a process-local production guarantee.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import AsyncIterator

from jvspatial.core.context import get_default_context

from app.exceptions import AuthorizationUnavailableError
from app.middleware.permissions_cache import isolated_permissions_cache
from app.models.edges import HAS_AUTHORIZATION_STATE
from app.models.nodes import Workspace, WorkspaceAuthorizationState
from app.services.authority_facts import relationship_literal
from app.services.resource_location import locate_resource

_held: ContextVar[dict[str, tuple[object, object, bool]] | None] = ContextVar(
    "integral_workspace_authorization_locks", default=None
)


@dataclass(frozen=True)
class AuthorizationRevision:
    workspace_id: str
    revision: int


def authorization_state_id(workspace_id: str) -> str:
    """Return the deterministic identity of the workspace's rooted revision."""
    return f"n.WorkspaceAuthorizationState.{workspace_id}"


@asynccontextmanager
async def authorization_scope(
    workspace_id: str,
    *,
    authority_mutation: bool = False,
    shared: bool | None = None,
) -> AsyncIterator[AuthorizationRevision]:
    """Order a protected effect or ACL mutation with every peer using this seam.

    No network/provider side effect belongs in this transaction. Their
    dispatch intent needs the same lock, and subsequent provider results
    require reconciliation. Released bytes cannot be recalled by a revision.
    """
    from app.services.app_operations.transaction_scope import (
        measure_write_lock,
        postgres_graph_transaction,
    )

    if not workspace_id or (shared is not None and type(shared) is not bool):
        raise AuthorizationUnavailableError()
    with isolated_permissions_cache():
        async with postgres_graph_transaction() as transaction:
            held = _held.get() or {}
            existing = held.get(workspace_id)
            if existing is not None and (
                existing[0] is not asyncio.current_task()
                or existing[1] is not transaction
            ):
                raise AuthorizationUnavailableError()
            mode = existing[2] if shared is None and existing else bool(shared)
            if authority_mutation:
                mode = False
            if existing is not None and existing[2] and not mode:
                # Never attempt a shared-to-exclusive upgrade: two peers could
                # deadlock. The outer command must choose exclusive admission.
                raise AuthorizationUnavailableError()
            state_id = authorization_state_id(workspace_id)
            # First use needs an exclusive logical key to create the rooted
            # state exactly once. An established nested shared holder must
            # fail closed on missing state instead of upgrading its lock.
            if mode and existing is None:
                mode = await transaction.get("node", state_id) is not None
            with measure_write_lock("workspace_advisory", shared=mode):
                await transaction.advisory_lock(
                    "integral-workspace-authorization-v1", workspace_id, shared=mode
                )
            if await locate_resource("workspace", workspace_id) is None:
                raise AuthorizationUnavailableError()
            with measure_write_lock("workspace_state_row", shared=mode):
                row = await transaction.lock_record("node", state_id, shared=mode)
            if row is None:
                if mode:
                    raise AuthorizationUnavailableError()
                workspace = await Workspace.find_one({"id": workspace_id})
                if workspace is None:
                    raise AuthorizationUnavailableError()
                state = await WorkspaceAuthorizationState.create(
                    id=state_id, workspace_id=workspace_id
                )
                await workspace.connect(state, edge=HAS_AUTHORIZATION_STATE)
                with measure_write_lock("workspace_state_row"):
                    row = await transaction.lock_record("node", state_id)
            if (
                row is None
                or row.get("id") != state_id
                or row.get("entity") != WorkspaceAuthorizationState._entity_name()
                or relationship_literal(row, "workspace_id", {workspace_id})
                != workspace_id
            ):
                raise AuthorizationUnavailableError()
            context = get_default_context()
            state = await context._deserialize_entity(WorkspaceAuthorizationState, row)
            edges = await context.find_edges_between(
                workspace_id, state_id, edge_class=HAS_AUTHORIZATION_STATE
            )
            if state is None or state.workspace_id != workspace_id or len(edges) != 1:
                raise AuthorizationUnavailableError()
            revision = (row.get("context") or {}).get("revision")
            if (
                not isinstance(revision, int)
                or isinstance(revision, bool)
                or revision < 0
            ):
                raise AuthorizationUnavailableError()
            if authority_mutation:
                row = await transaction.find_one_and_update(
                    "node",
                    {
                        "id": state_id,
                        "entity": WorkspaceAuthorizationState._entity_name(),
                    },
                    {"$inc": {"context.revision": 1}},
                )
                if (
                    row is None
                    or type((row.get("context") or {}).get("revision")) is not int
                    or (row.get("context") or {}).get("revision") != revision + 1
                ):
                    raise AuthorizationUnavailableError()
                revision += 1
            token = _held.set(
                {
                    **held,
                    workspace_id: (
                        asyncio.current_task(),
                        transaction,
                        mode and (existing is None or existing[2]),
                    ),
                }
            )
            try:
                yield AuthorizationRevision(workspace_id, revision)
            finally:
                _held.reset(token)
