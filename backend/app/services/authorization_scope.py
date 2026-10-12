"""Shared-store authorization ordering seam for workspace-bound effects.

PostgreSQL holds the workspace row lock until graph/receipt commit. Callers
must re-evaluate current authority *inside* this scope, then perform the
protected effect before leaving. Authority mutations bump the revision in
that same transaction. This is infrastructure, not an implicit grant.

Stores without graph transactions deliberately refuse this strong seam;
callers cannot silently turn it into a process-local production guarantee.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

from app.exceptions import AuthorizationUnavailableError
from app.middleware.permissions_cache import isolated_permissions_cache
from app.models.edges import HAS_AUTHORIZATION_STATE
from app.models.nodes import Workspace, WorkspaceAuthorizationState
from app.services.resource_location import locate_resource


@dataclass(frozen=True)
class AuthorizationRevision:
    workspace_id: str
    revision: int


def authorization_state_id(workspace_id: str) -> str:
    """Return the deterministic identity of the workspace's rooted revision."""
    return f"n.WorkspaceAuthorizationState.{workspace_id}"


@asynccontextmanager
async def authorization_scope(
    workspace_id: str, *, authority_mutation: bool = False
) -> AsyncIterator[AuthorizationRevision]:
    """Order a protected effect or ACL mutation with every peer using this seam.

    No network/provider side effect belongs in this transaction. Their
    dispatch intent needs the same lock, and subsequent provider results
    require reconciliation. Released bytes cannot be recalled by a revision.
    """
    from app.services.app_operations.transaction_scope import postgres_graph_transaction

    if not workspace_id:
        raise AuthorizationUnavailableError()
    with isolated_permissions_cache():
        async with postgres_graph_transaction() as transaction:
            # The no-op update acquires SELECT FOR UPDATE on the held connection,
            # and serializes first-time state creation without modifying settings.
            workspace_row = await transaction.find_one_and_update(
                "node",
                {"id": workspace_id, "entity": Workspace._entity_name()},
                {"$inc": {"context.storage_bytes_used": 0}},
            )
            if workspace_row is None:
                raise AuthorizationUnavailableError()
            workspace = await Workspace.find_one({"id": workspace_id})
            if (
                workspace is None
                or await locate_resource("workspace", workspace_id) is None
            ):
                raise AuthorizationUnavailableError()
            state_id = authorization_state_id(workspace_id)
            state = await WorkspaceAuthorizationState.find_one({"id": state_id})
            if state is None:
                state = await WorkspaceAuthorizationState.create(
                    id=state_id, workspace_id=workspace_id
                )
                await workspace.connect(state, edge=HAS_AUTHORIZATION_STATE)
            context = await workspace.get_context()
            edges = await context.find_edges_between(
                workspace.id, state.id, edge_class=HAS_AUTHORIZATION_STATE
            )
            if state.workspace_id != workspace_id or len(edges) != 1:
                raise AuthorizationUnavailableError()
            row = await transaction.find_one_and_update(
                "node",
                {"id": state_id, "entity": WorkspaceAuthorizationState._entity_name()},
                {"$inc": {"context.revision": 1 if authority_mutation else 0}},
            )
            if row is None:
                raise AuthorizationUnavailableError()
            revision = (row.get("context") or {}).get("revision")
            if (
                not isinstance(revision, int)
                or isinstance(revision, bool)
                or revision < 0
            ):
                raise AuthorizationUnavailableError()
            yield AuthorizationRevision(workspace_id, revision)
