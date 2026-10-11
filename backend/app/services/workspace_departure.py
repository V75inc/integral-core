"""Join membership removal to scoped ownership and grant cleanup."""

from app.api.errors import BadRequestError, ResourceNotFoundError
from app.models.edges import IS_MEMBER_OF
from app.models.nodes import User, Workspace
from app.services.authority_facts import relationship_flag, relationship_literal
from app.services.grant_history import grant_baseline, record_grant_change
from app.services.ownership_transfer import reassign_departing_member_ownership
from app.services.permissions_process_cache import invalidate_user_aliases
from app.services.sharing import revoke_workspace_resource_grants
from app.services.workspace_permissions import read_member_facts


async def remove_workspace_membership(
    *, member: User, workspace_id: str, clear_active_workspace: bool = False
) -> bool:
    """Clean up before deleting membership; PostgreSQL commits all changes together.

    JSON/memory development stores have no graph transaction. Their ordered
    fallback preserves membership on a cleanup failure and keeps replacement
    ownership edges present if a transfer is interrupted.
    """
    from app.services.membership_write_scope import membership_write_scope

    async with membership_write_scope(workspace_id):
        current = await User.find_one({"id": member.id})
        removed = await _remove_membership(
            current, workspace_id, clear_active_workspace
        )
    invalidate_user_aliases(member)
    return removed


async def _remove_membership(
    member: User | None, workspace_id: str, clear_active_workspace: bool
) -> bool:
    """Apply the complete departure inside the caller's store scope."""
    if member is None:
        raise ResourceNotFoundError(message="User not found")
    workspace = await Workspace.find_one({"id": workspace_id})
    if workspace is None:
        raise ResourceNotFoundError(message="Workspace not found")
    context = await member.get_context()
    edges = await context.find_edges_between(
        member.id, workspace_id, edge_class=IS_MEMBER_OF
    )
    row = await read_member_facts(member, workspace_id)
    if row is not None:
        relationship_flag(row, "can_create_apps")
        relationship_flag(row, "can_create_tracks")
    if (
        row is not None
        and relationship_literal(row, "role", {"owner", "admin", "member", "guest"})
        == "owner"
    ):
        raise BadRequestError(message="Cannot remove the workspace owner")
    if not edges:
        return False
    await grant_baseline(
        source_id=member.id, target_id=workspace.id, grant_class=IS_MEMBER_OF, grant=row
    )
    await reassign_departing_member_ownership(member, workspace_id)
    await revoke_workspace_resource_grants(member, workspace_id)
    if clear_active_workspace and member.active_workspace_id == workspace_id:
        member.active_workspace_id = ""
        member.active_workspace_id_explicit = False
        await member.save()
    # Cleanup failures must propagate before membership disappears.
    for edge in edges:
        await edge.delete()
    await record_grant_change(
        source=member,
        target=workspace,
        grant_class=IS_MEMBER_OF,
        before=row,
        after=None,
    )
    return True
