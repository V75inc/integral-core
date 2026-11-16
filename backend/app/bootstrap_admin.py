"""Bootstrap admin user on first launch.

When ADMIN_EMAIL and ADMIN_PASSWORD are set, ensures a platform admin AuthUser
exists (via jvspatial AuthenticationService), plus an Integral graph User node,
catalog edge, and personal workspace.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from jvspatial.api.auth.models import User as AuthUser

from app.config import settings
from app.models.nodes import (
    USERS_REGISTRY_ID,
    WORKSPACES_REGISTRY_ID,
    User,
    Users,
    Workspaces,
)
from app.services.authentication import (
    AuthenticationStateConflict,
    account_creation_scope,
)

logger = logging.getLogger(__name__)

ADMIN_ROLE = "admin"
_MAX_ATTEMPTS = 5


def _hash_password(password: str) -> str:
    """Hash a password using the same path as jvspatial auth (bcrypt when available)."""
    from app.api.auth import _get_auth_service

    return _get_auth_service()._hash_password(password)


async def _find_user_by_email(email: str) -> Optional[AuthUser]:
    """Find AuthUser by email via the prime-database auth service context."""
    from app.api.auth import _get_auth_service

    return await _get_auth_service()._find_user_by_email(email)


def _is_transient_db_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(
        token in msg
        for token in (
            "connection",
            "connect",
            "timeout",
            "refused",
            "starting up",
            "cannot connect",
            "pool is closed",
        )
    )


async def _ensure_graph_profile(auth_user_id: str, display_name: str) -> User:
    """Create or return the graph User linked to an AuthUser id."""
    from jvspatial.core.context import get_default_context

    matches = await get_default_context().database.find(
        "node",
        {
            "entity": User._entity_name(),
            "$or": [
                {"context.user_id": auth_user_id},
                {"user_id": auth_user_id},
            ],
        },
        limit=2,
    )
    if len(matches) > 1:
        raise AuthenticationStateConflict(
            "Ambiguous bootstrap graph profile; operator recovery required"
        )
    if matches:
        profile = await User.find_one({"id": matches[0]["id"]})
        if profile is None or profile.user_id != auth_user_id:
            raise AuthenticationStateConflict(
                "Invalid bootstrap graph profile; operator recovery required"
            )
        return profile

    user_node = await User.create(
        user_id=auth_user_id,
        display_name=display_name,
        created_at=datetime.now(timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat(),
    )
    from app.services.app_graph import catalog_user

    await catalog_user(user_node)
    logger.info("Bootstrap admin graph User created: %s", user_node.id)
    return user_node


async def _promote_auth_user_to_admin(auth_user: AuthUser, auth_service) -> None:
    roles = list(auth_user.roles or [])
    if ADMIN_ROLE in roles:
        return
    roles.append(ADMIN_ROLE)
    auth_user.roles = roles
    auth_user._graph_context = auth_service.context
    await auth_service.context.save(auth_user)
    logger.info("Bootstrap admin: promoted %s to platform admin", auth_user.email)


async def _bootstrap_admin_once() -> None:
    from jvspatial.core.context import get_default_context

    # Account creation, rooted profile and workspace share the same lock and
    # commit. A second serving process must not observe a half-built identity.
    async with account_creation_scope(get_default_context().database):
        await _bootstrap_admin_locked()


async def _bootstrap_admin_locked() -> None:
    from app.api.auth import _get_auth_service
    from app.services.app_graph import catalog_user, catalog_workspace
    from app.services.personal_workspace import ensure_personal_workspace

    email = (settings.ADMIN_EMAIL or "").strip()
    password = settings.ADMIN_PASSWORD or ""
    display_name = (settings.ADMIN_NAME or email).strip()

    auth_service = _get_auth_service()
    existing = await auth_service._find_user_by_email(email)

    # Existing accounts retain their credential even when the configured
    # bootstrap password is short. A new account must pass before any writes.
    if existing is None and len(password) < 12:
        raise ValueError(
            "ADMIN_PASSWORD must be at least 12 characters; "
            "admin bootstrap cannot complete"
        )
    # Graph initialization can have failed before this startup stage. Catalog
    # helpers intentionally skip missing registries; bootstrap cannot report a
    # complete identity after silently creating floating profile/workspace Nodes.
    if (
        await Users.find_one({"id": USERS_REGISTRY_ID}) is None
        or await Workspaces.find_one({"id": WORKSPACES_REGISTRY_ID}) is None
    ):
        raise RuntimeError("Admin bootstrap graph registries are unavailable")

    if existing:
        if not existing.is_active:
            raise AuthenticationStateConflict(
                "Bootstrap account is inactive; operator recovery required"
            )
        await _promote_auth_user_to_admin(existing, auth_service)
        auth_user_id = existing.id
        logger.info(
            "Bootstrap admin: using existing auth account for %s (%s)",
            email,
            auth_user_id,
        )
    else:
        created = await auth_service.bootstrap_admin(email, password, display_name)
        if created is None:
            logger.info(
                "Bootstrap admin skipped: another account already has the "
                "platform admin role (ADMIN_EMAIL=%s)",
                email,
            )
            return
        auth_user_id = created.id
        logger.info("Bootstrap admin auth user created: %s (%s)", email, auth_user_id)

    user_node = await _ensure_graph_profile(auth_user_id, display_name)

    await catalog_user(user_node)
    workspace = await ensure_personal_workspace(user_node)
    # The general lazy helper logs catalog failures; startup must propagate
    # them so the complete bootstrap is rolled back instead of reporting ready.
    await catalog_workspace(workspace)


async def bootstrap_admin_if_needed() -> None:
    """Create admin user from env if ADMIN_EMAIL/ADMIN_PASSWORD set and needed."""
    if os.getenv("PYTEST_CURRENT_TEST") or os.getenv("TESTING"):
        return

    email = settings.ADMIN_EMAIL
    password = settings.ADMIN_PASSWORD

    if not email or not password:
        if email and not password:
            raise ValueError(
                "ADMIN_EMAIL is set but ADMIN_PASSWORD is missing; "
                "admin bootstrap cannot complete"
            )
        else:
            logger.info("Admin bootstrap skipped: set ADMIN_EMAIL and ADMIN_PASSWORD")
        return

    last_exc: Optional[BaseException] = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            await _bootstrap_admin_once()
            return
        except Exception as exc:
            last_exc = exc
            if attempt < _MAX_ATTEMPTS and _is_transient_db_error(exc):
                delay = 2 * attempt
                logger.warning(
                    "Admin bootstrap attempt %s/%s failed (%s); retrying in %ss",
                    attempt,
                    _MAX_ATTEMPTS,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
                continue
            logger.exception("Failed to bootstrap admin after %s attempt(s)", attempt)
            raise RuntimeError(
                "Admin bootstrap failed; server startup aborted"
            ) from exc

    if last_exc is not None:
        raise RuntimeError(
            "Admin bootstrap failed; server startup aborted"
        ) from last_exc
