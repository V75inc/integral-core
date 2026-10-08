"""Join membership removal to scoped ownership and grant cleanup."""

from app.api.errors import BadRequestError, ResourceNotFoundError
from app.models.edges import IS_MEMBER_OF
from app.models.nodes import User, Workspace
from app.services.app_operations.transaction_scope import (
    graph_transaction_available,
    postgres_graph_transaction,
)
from app.services.ownership_transfer import reassign_departing_member_ownership
from app.services.permissions_process_cache import invalidate_user_aliases
from app.services.sharing import revoke_workspace_resource_grants


async def remove_workspace_membership(
    *, member: User, workspace_id: str, clear_active_workspace: bool = False
) -> bool:
    """Clean up before deleting membership; PostgreSQL commits all changes together.

    JSON/SQLite development stores have no graph transaction. Their ordered
    fallback preserves membership on a cleanup failure and keeps replacement
    ownership edges present if a transfer is interrupted.
    """
    context = await member.get_context()
    if graph_transaction_available(context.database):
        async with postgres_graph_transaction(context.database):
            # Rebind entities to the held graph transaction rather than writing
            # through the request context's already-hydrated nodes.
            current = await User.get(member.id)
            removed = await _remove_membership(
                current, workspace_id, clear_active_workspace
            )
    else:
        removed = await _remove_membership(member, workspace_id, clear_active_workspace)
    invalidate_user_aliases(member)
    return removed


async def _remove_membership(
    member: User | None, workspace_id: str, clear_active_workspace: bool
) -> bool:
    """Apply the complete departure inside the caller's store scope."""
    if member is None:
        raise ResourceNotFoundError(message="User not found")
    workspace = await Workspace.get(workspace_id)
    if workspace is None:
        raise ResourceNotFoundError(message="Workspace not found")
    context = await member.get_context()
    edges = await context.find_edges_between(
        member.id, workspace_id, edge_class=IS_MEMBER_OF
    )
    if any(edge.role == "owner" for edge in edges):
        raise BadRequestError(message="Cannot remove the workspace owner")
    if not edges:
        return False
    await reassign_departing_member_ownership(member, workspace_id)
    await revoke_workspace_resource_grants(member, workspace_id)
    if clear_active_workspace and member.active_workspace_id == workspace_id:
        member.active_workspace_id = ""
        member.active_workspace_id_explicit = False
        await member.save()
    # Cleanup failures must propagate before membership disappears.
    for edge in edges:
        await edge.delete()
    return True
