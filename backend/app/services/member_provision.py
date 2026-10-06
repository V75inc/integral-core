"""Provision a workspace member with a login account (Core hire overlay)."""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from jvspatial.api.auth.models import User as AuthUser
from jvspatial.api.auth.models import UserCreate

from app.config import settings
from app.models.edges import IS_MEMBER_OF
from app.models.nodes import User, Workspace
from app.services.app_graph import catalog_user
from app.services.email_service import render_member_provision_email, send_email
from app.services.personal_workspace import ensure_personal_workspace
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


async def _graph_user_for_auth(auth_user_id: str) -> Optional[User]:
    matches = await User.find({"context.user_id": auth_user_id})
    if matches:
        return matches[0]
    return None


async def _find_auth_user_by_email(email_norm: str) -> Optional[AuthUser]:
    if not email_norm:
        return None
    try:
        exact = await AuthUser.find({"context.email": email_norm})
        if exact:
            return exact[0]
    except Exception:
        pass
    try:
        rows = await AuthUser.find({})
        for row in rows or []:
            if str(getattr(row, "email", "") or "").strip().lower() == email_norm:
                return row
    except Exception:
        logger.exception("AuthUser scan failed during member provision")
    return None


async def _already_member(user: User, workspace: Workspace) -> bool:
    ctx = await user.get_context()
    edges = await ctx.find_edges_between(
        source_id=user.id, target_id=workspace.id, edge_class=IS_MEMBER_OF
    )
    return bool(edges)


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
    from app.api.auth import _get_auth_service
    from app.api.errors import BadRequestError

    email_norm = str(email or "").strip().lower()
    name = (display_name or "").strip() or email_norm

    created = False
    credentials_emailed = False
    temp_password: Optional[str] = None

    auth_user = await _find_auth_user_by_email(email_norm)
    if auth_user is None:
        temp_password = generate_temp_password()
        auth_service = _get_auth_service()
        try:
            user_response = await auth_service.register_user(
                UserCreate(email=email_norm, password=temp_password)
            )
        except ValueError as exc:
            raise BadRequestError(message=str(exc)) from exc
        auth_user_id = user_response.id
        now_iso = datetime.now(timezone.utc).isoformat()
        user_node = await User.create(
            user_id=auth_user_id,
            display_name=name,
            email_verified=True,
            created_at=now_iso,
            updated_at=now_iso,
        )
        await catalog_user(user_node)
        try:
            await ensure_personal_workspace(user_node)
        except Exception:
            logger.exception("ensure_personal_workspace failed during member provision")
        set_must_change_password(user_node, True)
        await user_node.save()
        created = True
    else:
        user_node = await _graph_user_for_auth(auth_user.id)
        if user_node is None:
            now_iso = datetime.now(timezone.utc).isoformat()
            user_node = await User.create(
                user_id=auth_user.id,
                display_name=name or getattr(auth_user, "name", "") or email_norm,
                created_at=now_iso,
                updated_at=now_iso,
            )
            await catalog_user(user_node)
            try:
                await ensure_personal_workspace(user_node)
            except Exception:
                logger.exception(
                    "ensure_personal_workspace failed during member provision"
                )

    already_member = await _already_member(user_node, workspace)
    if not already_member:
        await user_node.connect(
            workspace,
            edge=IS_MEMBER_OF,
            role=role,
            joined_at=utc_now_iso(),
            can_create_apps=False,
            can_create_tracks=False,
        )

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

    _ = actor_id
    return {
        "user_id": user_node.id,
        "auth_user_id": user_node.user_id,
        "email": email_norm,
        "display_name": user_node.display_name,
        "created": created,
        "already_member": already_member,
        "credentials_emailed": credentials_emailed,
        "must_change_password": created,
    }
