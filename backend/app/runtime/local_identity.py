"""Atomic, installation-bound owner creation and native session issuance.

The launcher secret never enters a renderer or URL. Bootstrap runs at startup
on the managed PostgreSQL store; ordinary registration cannot claim the owner.
"""

from __future__ import annotations

import asyncio
import secrets
from datetime import datetime, timezone

from jvspatial.api.auth.models import User as AuthUser
from jvspatial.core import Object
from jvspatial.core.context import get_default_context

from app.config import settings
from app.models.edges import CATALOGS, IS_MEMBER_OF
from app.models.nodes import (
    USERS_REGISTRY_ID,
    WORKSPACES_REGISTRY_ID,
    User,
    Users,
    Workspace,
    Workspaces,
)
from app.runtime.composition import owner_ids, runtime
from app.services.app_graph import catalog_workspace
from app.services.app_operations.transaction_scope import postgres_graph_transaction
from app.services.authentication import get_authentication_service
from app.utils.time import utc_now_iso

_bootstrap_lock = asyncio.Lock()
_MARKER_ID = "o.DesktopIdentity.singleton"


class DesktopIdentity(Object):
    """Private scalar installation record; the owner itself is a rooted Node."""

    installation_id: str
    auth_user_id: str
    user_id: str
    workspace_id: str


async def _validate_store(database, marker: DesktopIdentity | None) -> None:
    """Read primary rows, including malformed identities that cannot hydrate."""
    auth_id, user_id = owner_ids()
    for collection, entity, expected in (
        ("object", AuthUser._entity_name(), auth_id),
        ("node", User._entity_name(), user_id),
    ):
        rows = await database.find(collection, {"entity": entity}, limit=2)
        if marker is None:
            valid = not rows
        else:
            valid = len(rows) == 1 and rows[0].get("id") == expected
        if not valid:
            raise RuntimeError(
                "This store is not an eligible single-owner desktop installation. Preserve it and export to a new installation."
            )
    invalid_workspace = await database.find(
        "node",
        {
            "entity": Workspace._entity_name(),
            **({"context.kind": {"$ne": "personal"}} if marker else {}),
        },
        limit=1,
    )
    if invalid_workspace:
        raise RuntimeError(
            "Desktop requires a fresh store or its own private workspace backup"
        )
    foreign_membership = await database.find(
        "edge",
        {
            "entity": IS_MEMBER_OF.__name__,
            "$or": [{"source": {"$ne": user_id}}, {"context.role": {"$ne": "owner"}}],
        },
        limit=1,
    )
    if foreign_membership:
        raise RuntimeError("Desktop store contains incompatible membership grants")
    if marker and (
        marker.installation_id != settings.INTEGRAL_DESKTOP_INSTALLATION_ID
        or marker.auth_user_id != auth_id
        or marker.user_id != user_id
    ):
        raise RuntimeError("Desktop owner belongs to a different installation")


async def bootstrap_owner() -> None:
    """Create exactly one owner and rooted workspace, or fail without conversion."""
    if runtime().identity != "installation_owner":
        return
    async with _bootstrap_lock:
        async with postgres_graph_transaction() as database:
            ctx = get_default_context()
            marker = await ctx.get(DesktopIdentity, _MARKER_ID)
            await _validate_store(database, marker)
            if marker:
                owner = await ctx.get(User, marker.user_id)
                workspace = await ctx.get(Workspace, marker.workspace_id)
                auth = await get_authentication_service().context.get(
                    AuthUser, marker.auth_user_id
                )
                if (
                    owner is None
                    or workspace is None
                    or auth is None
                    or not auth.is_active
                    or owner.user_id != auth.id
                ):
                    raise RuntimeError("Desktop owner identity needs recovery")
                return
            users = await Users.get(USERS_REGISTRY_ID)
            workspaces = await Workspaces.get(WORKSPACES_REGISTRY_ID)
            if users is None or workspaces is None:
                raise RuntimeError("Desktop graph registries are unavailable")
            auth_id, user_id = owner_ids()
            auth_service = get_authentication_service()
            auth = AuthUser(
                id=auth_id,
                email="owner@integral.local",
                password_hash=auth_service._hash_password(secrets.token_urlsafe(48)),
                name="Owner",
                roles=["user", "admin"],
                is_active=True,
                created_at=datetime.now(timezone.utc),
            )
            await auth_service.context.save(auth)
            now = utc_now_iso()
            owner = await User.create(
                id=user_id,
                user_id=auth_id,
                display_name="Owner",
                created_at=now,
                updated_at=now,
            )
            await users.connect(owner, edge=CATALOGS, cataloged_at=now)
            workspace = await Workspace.create(
                kind="personal",
                workspace_type="personal",
                name="Personal",
                name_fold="personal",
                created_at=now,
                updated_at=now,
            )
            await owner.connect(
                workspace,
                edge=IS_MEMBER_OF,
                role="owner",
                joined_at=now,
                can_create_apps=True,
                can_create_tracks=True,
            )
            await catalog_workspace(workspace)
            await DesktopIdentity.create(
                id=_MARKER_ID,
                installation_id=settings.INTEGRAL_DESKTOP_INSTALLATION_ID,
                auth_user_id=auth_id,
                user_id=user_id,
                workspace_id=workspace.id,
            )
            from app.services.app_operations.event_outbox import insert_revision_event

            for resource, action in (
                (owner, "user.create"),
                (workspace, "workspace.create"),
            ):
                await insert_revision_event(
                    transaction=database,
                    workspace_id=workspace.id,
                    namespace="desktop-bootstrap-v1",
                    subject_id=resource.id,
                    revision=1,
                    event={
                        "actor_kind": "system",
                        "actor_id": "desktop_bootstrap",
                        "action": action,
                        "resource_type": type(resource).__name__,
                        "resource_id": resource.id,
                        "before": None,
                        "after": await resource.export(flat=True),
                        "scope": (
                            f"user:{owner.id}"
                            if resource is owner
                            else f"ws:{workspace.id}"
                        ),
                    },
                )
            # The logging database is registered later in startup. Keep these
            # committed facts pending until the post-registration consumer runs.


async def issue_session() -> dict:
    """Issue normal revocable tokens after the API validates native possession."""
    from app.api.errors import MissingAuthenticationError

    auth_id, _ = owner_ids()
    service = get_authentication_service()
    current = await service.context.get(AuthUser, auth_id)
    if current is None or not current.is_active:
        raise MissingAuthenticationError(message="Local owner is unavailable")
    token, _ = service._generate_jwt_token(
        current.id,
        current.email,
        roles=service._get_user_roles(current),
        permissions=list(service._get_effective_permissions_for_user(current)),
    )
    jti = service._decode_jwt_token(token)["jti"]
    refresh, expires_at = await service._generate_and_store_refresh_token(
        current.id, jti, "Integral Desktop", "127.0.0.1"
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": service.jwt_expire_minutes * 60,
        "refresh_token": refresh,
        "refresh_expires_in": int(
            (expires_at - datetime.now(timezone.utc)).total_seconds()
        ),
    }


async def preserve_owner_workspace(workspace_id: str) -> None:
    """Keep the installation's recovery/default workspace addressable."""
    if runtime().identity != "installation_owner":
        return
    from app.api.errors import ResourceConflictError

    marker = await get_default_context().get(DesktopIdentity, _MARKER_ID)
    if marker is None or marker.workspace_id == workspace_id:
        raise ResourceConflictError(
            message="Keep this installation's default private workspace. Its records and Apps can be managed individually."
        )
