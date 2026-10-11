"""Provision a workspace member with a login account (Core hire overlay)."""

from __future__ import annotations

import logging
import re
import secrets
from contextlib import nullcontext
from typing import Any, Dict, Optional

from jvspatial.api.auth.models import User as AuthUser
from jvspatial.api.auth.models import UserCreate
from jvspatial.core.context import get_default_context
from pydantic import ValidationError

from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    ResourceConflictError,
)
from app.config import settings
from app.exceptions import AuthorizationUnavailableError
from app.models.nodes import User, Workspace
from app.schemas.api.workspaces_hire import ProvisionWorkspaceMemberRequest
from app.services import workspace_membership as membership
from app.services.app_graph import catalog_user
from app.services.app_operations.transaction_scope import (
    graph_transaction_available,
    postgres_graph_transaction,
)
from app.services.authentication import get_authentication_service
from app.services.authority_facts import (
    primary_auth_record,
    primary_node_record,
    required_node_text,
)
from app.services.email_identity import verified_current_email
from app.services.email_service import render_member_provision_email, send_email
from app.services.entry_write_scope import (
    entry_write_scope,
    is_postgres_entry_transaction,
)
from app.services.membership_write_scope import membership_write_scope
from app.services.permissions import get_user_node
from app.services.personal_workspace import ensure_personal_workspace
from app.services.resource_location import locate_resource
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)

MUST_CHANGE_PASSWORD_KEY = "must_change_password"


def generate_temp_password() -> str:
    raw = secrets.token_urlsafe(12)
    minimum = max(int(getattr(settings, "PASSWORD_MIN_LENGTH", 8) or 8), 8)
    if len(raw) < minimum:
        raw = f"{raw}Aa1!"
    return raw


def set_must_change_password(user: User, value: bool) -> None:
    prefs = dict(user.preferences or {})
    if value:
        prefs[MUST_CHANGE_PASSWORD_KEY] = True
    else:
        prefs.pop(MUST_CHANGE_PASSWORD_KEY, None)
    user.preferences = prefs


def must_change_password(user: User) -> bool:
    return bool((user.preferences or {}).get(MUST_CHANGE_PASSWORD_KEY))


async def _find_auth_user_by_email(email_norm: str) -> Optional[AuthUser]:
    """Require unique primary email identity; never suppress a failed lookup."""
    database = get_default_context().database
    try:
        rows = await database.find(
            "object",
            {
                "entity": AuthUser._entity_name(),
                "context.email": {
                    "$regex": "^" + re.escape(email_norm) + "$",
                    "$options": "i",
                },
            },
            limit=2,
        )
        if not rows:
            return None
        if len(rows) != 1:
            raise AuthorizationUnavailableError()
        identity = await get_authentication_service().context.get(
            AuthUser, rows[0]["id"]
        )
        if (
            identity is None
            or not identity.is_active
            or identity.email.strip().lower() != email_norm
        ):
            raise InsufficientPermissionsError(message="Account is unavailable")
        return identity
    except (AuthorizationUnavailableError, InsufficientPermissionsError):
        raise
    except Exception as exc:
        raise AuthorizationUnavailableError() from exc


async def _lock_identity(user: User) -> None:
    """Hold current identity/profile rows before the workspace mutation fence."""
    database = get_default_context().database
    if not is_postgres_entry_transaction(database):
        return
    if user.user_id:
        row = await primary_auth_record(database, user.user_id)
        if row is None:
            raise InsufficientPermissionsError(message="Account is unavailable")
        locked = await database.find_one_and_update(
            "object",
            {"id": user.user_id, "entity": AuthUser._entity_name()},
            {"$set": {}},
        )
        if locked is None or locked["context"].get("is_active") is not True:
            raise InsufficientPermissionsError(message="Account is unavailable")
    if (
        await database.find_one_and_update(
            "node", {"id": user.id, "entity": User._entity_name()}, {"$set": {}}
        )
        is None
    ):
        raise AuthorizationUnavailableError()


async def provision_workspace_member(
    *,
    workspace: Workspace,
    email: str,
    display_name: str,
    role: str = "member",
    send_credentials: bool = True,
    welcome_message: Optional[str] = None,
    credentials_email: Optional[str] = None,
    actor_id: str,
) -> Dict[str, Any]:
    """Provision through current canonical membership admission and audit."""
    try:
        body = ProvisionWorkspaceMemberRequest.model_validate(
            {
                "email": email,
                "display_name": display_name,
                "role": role,
                "send_credentials": send_credentials,
                "welcome_message": welcome_message,
                "credentials_email": credentials_email,
            }
        )
    except ValidationError as exc:
        raise BadRequestError(message="Invalid provisioning request") from exc
    email_norm = str(body.email).strip().lower()
    name = body.display_name.strip()
    if not name:
        raise BadRequestError(message="Display name is required")
    if body.credentials_email and str(body.credentials_email).lower() != email_norm:
        raise BadRequestError(
            message="Credentials must be delivered to the account email"
        )
    if (
        is_postgres_entry_transaction(get_default_context().database)
        and send_credentials
    ):
        raise ResourceConflictError(
            message="Credential delivery requires a committed provisioning call"
        )
    # Preflight denies unauthorized callers before account lookup/creation.
    actor, _, owner = await membership._manager(actor_id, workspace.id)
    if body.role == "admin" and not owner:
        raise InsufficientPermissionsError(
            message="Only the workspace owner can grant admin"
        )
    created = False
    temp_password: Optional[str] = None
    credentials_emailed = False
    outbox_id = None
    database = get_default_context().database
    transaction = (
        postgres_graph_transaction(database)
        if graph_transaction_available(database)
        else nullcontext()
    )
    async with entry_write_scope(f"member-provision-email:{email_norm}"), transaction:
        actor = await get_user_node(actor_id)
        if actor is None:
            raise InsufficientPermissionsError(message="Access denied")
        auth_user = await _find_auth_user_by_email(email_norm)
        user_node = await get_user_node(auth_user.id) if auth_user else None
        if auth_user and user_node is None:
            raise AuthorizationUnavailableError()
        # Stable row ordering prevents two provisioning calls reversing actor/recipient locks.
        identities = {actor.id: actor}
        if user_node:
            identities[user_node.id] = user_node
        for identity in sorted(
            identities.values(), key=lambda value: value.user_id or value.id
        ):
            await _lock_identity(identity)
        async with membership_write_scope(workspace.id):
            actor, workspace, owner = await membership._manager(actor_id, workspace.id)
            if body.role == "admin" and not owner:
                raise InsufficientPermissionsError(
                    message="Only the workspace owner can grant admin"
                )
            if auth_user is not None:
                user_node = await get_user_node(auth_user.id)
                proof_row = (
                    await primary_node_record(
                        get_default_context().database, User, user_node.id
                    )
                    if user_node
                    else None
                )
                if proof_row is not None and (
                    type(proof_row["context"].get("email_verified")) is not bool
                    or (
                        "email_verified" in proof_row
                        and (
                            type(proof_row["email_verified"]) is not bool
                            or proof_row["email_verified"]
                            != proof_row["context"]["email_verified"]
                        )
                    )
                ):
                    raise AuthorizationUnavailableError()
                if (
                    user_node is None
                    or proof_row is None
                    or proof_row["context"].get("email_verified") is not True
                    or required_node_text(
                        proof_row, "verified_email", allow_empty=True
                    ).lower()
                    != email_norm
                    or await verified_current_email(user_node) != email_norm
                ):
                    raise InsufficientPermissionsError(
                        message="Existing account must verify its current email"
                    )
            else:
                temp_password = generate_temp_password()
                try:
                    response = await get_authentication_service().register_user(
                        UserCreate(email=email_norm, password=temp_password)
                    )
                except ValueError as exc:
                    raise ResourceConflictError(
                        message="Account cannot be provisioned"
                    ) from exc
                now = utc_now_iso()
                user_node = await User.create(
                    user_id=response.id,
                    display_name=name,
                    email_verified=False,
                    created_at=now,
                    updated_at=now,
                )
                await catalog_user(user_node)
                personal = await ensure_personal_workspace(user_node)
                if await locate_resource("workspace", personal.id) is None:
                    raise AuthorizationUnavailableError()
                set_must_change_password(user_node, True)
                await user_node.save()
                created = True
            old = await membership._membership(user_node, workspace.id)
            already_member = old is not None
            if old is not None:
                actual = membership._snapshot(old)
                membership._ceiling(
                    actor,
                    user_node,
                    owner,
                    actual,
                    {
                        "role": body.role,
                        "can_create_apps": False,
                        "can_create_tracks": False,
                    },
                )
                if actual["role"] != body.role:
                    raise ResourceConflictError(
                        message="Existing membership has different rights"
                    )
                outcome = "unchanged"
            else:
                actual = await membership.add_member(
                    actor_id=actor_id,
                    workspace_id=workspace.id,
                    member_id=user_node.id,
                    role=body.role,
                )
                outcome = "created"
            if created:
                outbox_id = await membership._record_change(
                    actor,
                    workspace,
                    "workspace.member_provision",
                    None,
                    {**actual, "account_created": True},
                )
    await membership._deliver(outbox_id)
    deliver_to = str(credentials_email or "").strip().lower() or email_norm
    if send_credentials and deliver_to:
        login_url = f"{settings.APP_BASE_URL.rstrip('/')}/login"
        ws_name = str(getattr(workspace, "name", "") or "your organization").strip()
        message = render_member_provision_email(
            recipient_email=deliver_to,
            recipient_name=name,
            workspace_name=ws_name,
            login_url=login_url,
            account_email=email_norm,
            temp_password=temp_password if created else None,
            welcome_message=(welcome_message or "").strip() or None,
        )
        message.workspace_id = str(getattr(workspace, "id", "") or "")
        message.source_kind = "member_provision"
        message.source_id = str(user_node.id or "")
        message.actor_user_id = str(actor_id or "")
        credentials_emailed = await send_email(message)

    return {
        "user_id": user_node.id,
        "auth_user_id": user_node.user_id,
        "email": email_norm,
        "display_name": user_node.display_name,
        "created": created,
        "already_member": already_member,
        "credentials_emailed": credentials_emailed,
        "must_change_password": must_change_password(user_node),
        "grant_outcome": outcome,
        "granted_role": actual["role"],
        "membership_id": actual["membership_id"],
        "grant_revision": actual["grant_revision"],
    }
