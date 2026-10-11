"""Current, versioned member administration without replacing membership lives.

The PostgreSQL workspace fence joins invitation writers. Development JSON
storage only serializes local callers and cannot promise atomic rollback.
"""

import logging
from typing import Any

from jvspatial.core.context import get_default_context

from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from app.exceptions import AuthorizationUnavailableError
from app.models.edges import IS_MEMBER_OF
from app.models.nodes import User, Workspace
from app.services.authority_facts import relationship_flag, relationship_literal
from app.services.entry_write_scope import is_postgres_entry_transaction
from app.services.membership_write_scope import membership_write_scope
from app.services.permissions import get_user_node
from app.services.permissions_process_cache import invalidate_user_aliases
from app.services.resource_location import locate_resource
from app.services.workspace_governance import owner_governance_allowed
from app.services.workspace_kind import is_collaborative_kind
from app.services.workspace_permissions import can_access_workspace, read_member_facts
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)


async def _manager(actor_id: str, workspace_id: str) -> tuple[User, Workspace, bool]:
    """Read current canonical authority inside the held mutation scope."""
    actor = await get_user_node(actor_id)
    workspace = await Workspace.find_one({"id": workspace_id})
    if (
        actor is None
        or workspace is None
        or await locate_resource("workspace", workspace_id) is None
    ):
        raise InsufficientPermissionsError(message="Access denied")
    if not is_collaborative_kind(workspace.kind):
        raise BadRequestError(message="Personal workspaces have no members to manage")
    role = await can_access_workspace(actor.id, workspace.id)
    owner = role == "owner" and owner_governance_allowed()
    if not owner and role != "admin":
        raise InsufficientPermissionsError(message="Access denied")
    return actor, workspace, owner


async def _membership(member: User, workspace_id: str) -> dict | None:
    """Admit one primary relationship without role/flag/version coercion."""
    row = await read_member_facts(member, workspace_id)
    if row is None:
        return None
    if (
        not isinstance(row.get("id"), str)
        or not row["id"]
        or not isinstance(row.get("context"), dict)
    ):
        raise AuthorizationUnavailableError()
    relationship_flag(row, "can_create_apps")
    relationship_flag(row, "can_create_tracks")
    revision = row["context"].get("grant_revision", 0)
    if type(revision) is not int or revision < 0:
        raise AuthorizationUnavailableError()
    if "grant_revision" in row and (
        type(row["grant_revision"]) is not int or row["grant_revision"] != revision
    ):
        raise AuthorizationUnavailableError()
    return row


def _snapshot(row: dict) -> dict:
    """Include actual rights and stable source identity in the audit fact."""
    return {
        "workspace_id": row["target"],
        "member_user_id": row["source"],
        "membership_id": row["id"],
        "role": relationship_literal(
            row, "role", {"owner", "admin", "member", "guest"}
        ),
        "can_create_apps": relationship_flag(row, "can_create_apps"),
        "can_create_tracks": relationship_flag(row, "can_create_tracks"),
        "joined_at": row["context"].get("joined_at"),
        "grant_revision": row["context"].get("grant_revision", 0),
    }


def _validate_values(role: str | None, apps: bool | None, tracks: bool | None) -> None:
    """Service callers must supply exact vocabulary and actual bools."""
    if role is not None and (
        type(role) is not str or role not in {"admin", "member", "guest"}
    ):
        raise BadRequestError(message="Invalid member role")
    if any(value is not None and type(value) is not bool for value in (apps, tracks)):
        raise BadRequestError(message="Creation rights must be booleans")


def _ceiling(
    actor: User, member: User, owner: bool, old: dict | None, values: dict
) -> None:
    """Reserve protected peers, self changes and delegation for stewardship."""
    if old is not None and old["role"] == "owner":
        raise BadRequestError(
            message="Cannot change the workspace owner via members API"
        )
    if owner:
        return
    if (
        actor.id == member.id
        or (
            old is not None
            and (
                old["role"] == "admin"
                or old["can_create_apps"]
                or old["can_create_tracks"]
            )
        )
        or values["role"] == "admin"
        or values["can_create_apps"]
        or values["can_create_tracks"]
    ):
        raise InsufficientPermissionsError(
            message="Only the workspace owner can manage protected membership rights"
        )


async def _record_change(
    actor: User, workspace: Workspace, action: str, before: dict | None, after: dict
) -> str | None:
    """Commit the primary audit intent with membership on PostgreSQL."""
    from app.services.app_operations.event_outbox import insert_revision_event
    from app.services.change_event import emit_change_event

    event: dict[str, Any] = {
        "actor_kind": "human",
        "actor_id": actor.id,
        "action": action,
        "resource_type": "Workspace",
        "resource_id": workspace.id,
        "before": before,
        "after": after,
        "scope": f"user:{actor.user_id or actor.id}",
    }
    database = get_default_context().database
    if is_postgres_entry_transaction(database):
        return await insert_revision_event(
            transaction=database,
            workspace_id=workspace.id,
            namespace="workspace-membership-v1",
            subject_id=after["membership_id"],
            revision=after["grant_revision"],
            event=event,
        )
    await emit_change_event(**event)
    return None


async def _deliver(outbox_id: str | None) -> None:
    """Deliver only committed intent, leaving sink failures recoverable."""
    from app.services.app_operations.event_outbox import deliver_operation_event

    database = get_default_context().database
    if outbox_id is None or is_postgres_entry_transaction(database):
        return
    try:
        await deliver_operation_event(outbox_id=outbox_id, database=database)
    except Exception:
        logger.exception("Workspace member audit delivery pending: %s", outbox_id)


async def add_member(
    *,
    actor_id: str,
    workspace_id: str,
    member_id: str,
    role: str = "member",
    can_create_apps: bool = False,
    can_create_tracks: bool = False,
) -> dict:
    """Create one membership; duplicate additions never replace existing rights."""
    if (
        role is None
        or type(can_create_apps) is not bool
        or type(can_create_tracks) is not bool
    ):
        raise BadRequestError(
            message="Explicit member role and boolean creation rights required"
        )
    _validate_values(role, can_create_apps, can_create_tracks)
    async with membership_write_scope(workspace_id):
        actor, workspace, owner = await _manager(actor_id, workspace_id)
        member = await get_user_node(member_id)
        if member is None:
            raise ResourceNotFoundError(message="User not found")
        row = await _membership(member, workspace_id)
        values = {
            "role": role,
            "can_create_apps": can_create_apps,
            "can_create_tracks": can_create_tracks,
        }
        _ceiling(actor, member, owner, _snapshot(row) if row else None, values)
        if row is not None:
            raise ResourceConflictError(message="User is already a workspace member")
        await member.connect(
            workspace, edge=IS_MEMBER_OF, joined_at=utc_now_iso(), **values
        )
        current = await _membership(member, workspace_id)
        if current is None:
            raise AuthorizationUnavailableError()
        after = _snapshot(current)
        outbox_id = await _record_change(
            actor, workspace, "workspace.member_add", None, after
        )
    invalidate_user_aliases(member)
    await _deliver(outbox_id)
    return {"message": "Member added", **after}


async def update_member(
    *,
    actor_id: str,
    workspace_id: str,
    member_id: str,
    role: str | None = None,
    can_create_apps: bool | None = None,
    can_create_tracks: bool | None = None,
) -> dict:
    """Update rights in place, preserving lifetime and all unrelated edge facts."""
    _validate_values(role, can_create_apps, can_create_tracks)
    if role is None and can_create_apps is None and can_create_tracks is None:
        raise BadRequestError(message="No fields to update")
    outbox_id = None
    async with membership_write_scope(workspace_id):
        actor, workspace, owner = await _manager(actor_id, workspace_id)
        member = await get_user_node(member_id)
        if member is None:
            raise ResourceNotFoundError(message="User not found")
        row = await _membership(member, workspace_id)
        if row is None:
            raise ResourceNotFoundError(
                message="User is not a member of this workspace"
            )
        before = _snapshot(row)
        values = {
            "role": role if role is not None else before["role"],
            "can_create_apps": (
                can_create_apps
                if can_create_apps is not None
                else before["can_create_apps"]
            ),
            "can_create_tracks": (
                can_create_tracks
                if can_create_tracks is not None
                else before["can_create_tracks"]
            ),
        }
        _ceiling(actor, member, owner, before, values)
        after = before
        if any(value != before[key] for key, value in values.items()):
            updates = {f"context.{key}": value for key, value in values.items()}
            updates.update({key: value for key, value in values.items() if key in row})
            if "grant_revision" in row:
                updates["grant_revision"] = before["grant_revision"] + 1
            result = await get_default_context().database.find_one_and_update(
                "edge",
                {
                    "id": row["id"],
                    "source": member.id,
                    "target": workspace.id,
                    "entity": IS_MEMBER_OF.__name__,
                },
                {"$set": updates, "$inc": {"context.grant_revision": 1}},
            )
            if result is None:
                raise AuthorizationUnavailableError()
            current = await _membership(member, workspace_id)
            if current is None:
                raise AuthorizationUnavailableError()
            after = _snapshot(current)
            outbox_id = await _record_change(
                actor, workspace, "workspace.member_update", before, after
            )
    invalidate_user_aliases(member)
    await _deliver(outbox_id)
    return {"message": "Member updated", **after}


async def remove_member(
    *, actor_id: str, workspace_id: str, member_id: str, leave: bool = False
) -> dict:
    """Fence current removal authority, cleanup and durable audit together."""
    from app.services.workspace_departure import _remove_membership

    outbox_id = None
    async with membership_write_scope(workspace_id):
        if leave:
            actor = await get_user_node(actor_id)
            workspace = await Workspace.find_one({"id": workspace_id})
            if (
                actor is None
                or workspace is None
                or await locate_resource("workspace", workspace_id) is None
            ):
                raise InsufficientPermissionsError(message="Access denied")
            member = await get_user_node(member_id)
            if member is None or member.id != actor.id:
                raise InsufficientPermissionsError(message="Access denied")
            owner = False
        else:
            actor, workspace, owner = await _manager(actor_id, workspace_id)
            member = await get_user_node(member_id)
            if member is None:
                raise ResourceNotFoundError(message="User not found")
        row = await _membership(member, workspace_id)
        if row is not None:
            before = _snapshot(row)
            if before["role"] == "owner":
                raise BadRequestError(message="Cannot remove the workspace owner")
            if not leave:
                _ceiling(actor, member, owner, before, before)
            await _remove_membership(member, workspace_id, leave)
            after = {
                **before,
                "role": None,
                "can_create_apps": False,
                "can_create_tracks": False,
                "grant_revision": before["grant_revision"] + 1,
                "removed": True,
            }
            outbox_id = await _record_change(
                actor,
                workspace,
                "workspace.member_leave" if leave else "workspace.member_remove",
                before,
                after,
            )
    invalidate_user_aliases(member)
    await _deliver(outbox_id)
    return {
        "message": "Workspace removed from your list" if leave else "Member removed",
        "workspace_id": workspace_id,
        "removed_member_id": member.id,
    }
