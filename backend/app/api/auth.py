"""Authentication API endpoints.

jvspatial provides built-in authentication endpoints when auth_enabled=True:
- POST /auth/login
- POST /auth/logout

Its built-in POST /auth/register is NOT exposed — ``app/main.py`` removes it
from the auth router at boot. It creates a jvspatial ``AuthUser`` and nothing
else, which yields a login with no ``User`` graph node, no Personal Workspace,
no verification OTP and no per-user context App. ``/auth/signup`` below is the
only supported registration path.

This module provides additional auth-related endpoints specific to Integral:
- POST /auth/signup (extended registration with name and organization support)
- GET /auth/me (get current user profile)
- PUT /auth/update-profile (update user profile)
"""

import contextlib
import inspect
import logging
from typing import Any, Dict, Optional

from fastapi import Request
from jvspatial.api import endpoint
from jvspatial.api.auth.models import User as AuthUser
from jvspatial.api.auth.models import UserCreate, UserLogin
from jvspatial.api.auth.service import AuthenticationService
from pydantic import ValidationError

from app.api.errors import (
    BadRequestError,
    MissingAuthenticationError,
    PasswordResetError,
    ResourceNotFoundError,
    UnprocessableEntityError,
)
from app.api.utils import export_node, get_user_id_from_request, resolve_principal_id
from app.config import settings
from app.models.edges import IS_MEMBER_OF
from app.models.nodes import User, Workspace
from app.schemas.api.auth import (
    ExtendedUserCreate,
    ForgotPasswordRequest,
    ResetPasswordRequest,
    VerifyEmailRequest,
)
from app.services.app_graph import catalog_user, catalog_workspace
from app.services.change_event import emit_change_event
from app.services.email_verification import (
    ERR_ALREADY_VERIFIED,
    ERR_ATTEMPTS_EXCEEDED,
    ERR_EXPIRED_CODE,
    consume_verification_code,
    create_verification_request,
    has_pending_code,
)
from app.services.permissions import get_user_node
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)

_SENSITIVE_PREFERENCE_KEYS = frozenset({"reset_token", "email_verification"})


def _scrub_sensitive_preferences(prefs: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not prefs:
        return {}
    return {k: v for k, v in prefs.items() if k not in _SENSITIVE_PREFERENCE_KEYS}


async def _self_user_view(user: User) -> Dict[str, Any]:
    """Export a User node for its OWNER, with credential material removed.

    Distinct from :func:`app.api.utils.public_user_view`, which narrows a
    user down for *other* users. The account owner legitimately sees their
    own profile fields — but never ``preferences.email_verification`` or
    ``preferences.reset_token``. The verification slot holds an unsalted
    SHA-256 of a **6-digit** OTP; a 10^6 space is exhaustible in
    milliseconds, so publishing the hash publishes the code and defeats
    the point of proving control of the address.

    Every self-serialization path in this module goes through here so a
    new one cannot forget the scrub (signup did, and shipped the hash in
    its response body).

    Both copies are scrubbed. ``Node.export()`` nests the model fields under
    ``context`` and the flattening ``update()`` COPIES them to the top level
    without removing ``context`` — so scrubbing only the flat key left the
    slot fully readable at ``user.context.preferences.email_verification``.
    ``/auth/me`` and ``/auth/update-profile`` shipped it that way too.
    """
    user_data = await user.export()
    context = user_data.get("context")
    if isinstance(context, dict):
        user_data.update(context)
    for target in (user_data, context):
        if isinstance(target, dict) and "preferences" in target:
            target["preferences"] = _scrub_sensitive_preferences(
                target.get("preferences")
            )
    return user_data


def _merge_user_preferences(
    existing: Optional[Dict[str, Any]], patch: Dict[str, Any]
) -> Dict[str, Any]:
    merged = dict(existing or {})
    for key, value in patch.items():
        if key in _SENSITIVE_PREFERENCE_KEYS:
            continue
        merged[key] = value
    return merged


def _get_auth_service() -> AuthenticationService:
    """Construct AuthenticationService from application settings."""
    from jvspatial.core.context import GraphContext
    from jvspatial.db import get_prime_database

    ctx = GraphContext(database=get_prime_database())
    kwargs: Dict[str, Any] = {"jwt_secret": settings.SECRET_KEY}
    params = inspect.signature(AuthenticationService.__init__).parameters
    if "registration_open" in params:
        kwargs["registration_open"] = settings.REGISTRATION_OPEN
    return AuthenticationService(ctx, **kwargs)


async def _enrich_from_auth_user(user_node: User, user_data: Dict[str, Any]) -> None:
    """Merge AuthUser email, name, and roles into user_data in-place."""
    if not user_node.user_id:
        await _attach_workspace_session_hints(user_node, user_data)
        return
    try:
        auth_user = await AuthUser.get(user_node.user_id)
        if auth_user:
            user_data["email"] = auth_user.email
            if auth_user.name:
                user_data["name"] = auth_user.name
            if auth_user.roles:
                user_data["roles"] = auth_user.roles
                user_data["role"] = _roles_to_frontend_role(auth_user.roles)
    except Exception:
        pass
    await _attach_workspace_session_hints(user_node, user_data)


async def _attach_workspace_session_hints(
    user: User, user_data: Dict[str, Any]
) -> None:
    """Set ``workspace_id`` (primary) and UI ``role`` from workspace memberships."""
    roles = list(user_data.get("roles") or [])
    is_platform_admin = "admin" in roles

    member_workspaces = await user.nodes(edge=["IS_MEMBER_OF"], node=["Workspace"])
    from app.services.workspace_kind import is_collaborative_kind

    org_workspaces = [ws for ws in member_workspaces if is_collaborative_kind(ws.kind)]

    primary_id: Optional[str] = None
    if org_workspaces:
        primary_id = org_workspaces[0].id

    if is_platform_admin:
        best_ui_role = "Super Admin"
    else:
        best_ui_role = user_data.get("role") or _roles_to_frontend_role(roles)

    if not is_platform_admin:
        ctx = await user.get_context()
        for ws in org_workspaces:
            edges = await ctx.find_edges_between(
                source_id=user.id, target_id=ws.id, edge_class=IS_MEMBER_OF
            )
            if not edges:
                continue
            edge = edges[0]
            memb_role = edge.role or "member"
            memb_role_norm = str(memb_role).lower()
            if memb_role_norm == "owner":
                best_ui_role = "Workspace Owner"
                if not primary_id:
                    primary_id = ws.id
            elif memb_role_norm in ("admin", "manager"):
                if best_ui_role == "Member":
                    best_ui_role = "Admin"
                if not primary_id:
                    primary_id = ws.id

    user_data["role"] = best_ui_role
    if primary_id:
        user_data["workspace_id"] = primary_id


def _roles_to_frontend_role(roles: list) -> str:
    """Map backend roles to frontend UserRole string."""
    if not roles:
        return "Member"
    if "admin" in roles:
        return "Super Admin"
    return "Member"


@endpoint("/auth/me", methods=["GET"], auth=True, tags=["Auth"])
async def get_current_user(request: Request) -> Dict[str, Any]:
    """Get the currently authenticated user's information.

    Resolve identity from ``request.state.user`` (set by auth middleware after JWT
    validation). Do not rely on ``user_id`` parameter injection, which is unreliable
    for some GET registrations when a parameter model or route ordering applies.
    """
    user_id = get_user_id_from_request(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    user = await get_user_node(user_id)
    if not user:
        auth_user = await AuthUser.get(user_id)
        if not auth_user:
            raise ResourceNotFoundError(message="User not found")
        user = await User.create(
            user_id=auth_user.id,
            display_name=auth_user.name or auth_user.email or "",
            created_at=utc_now_iso(),
            updated_at=utc_now_iso(),
        )
        with contextlib.suppress(Exception):
            await catalog_user(user)
        logger.info("Lazy-created User for AuthUser %s", auth_user.id)

    user_data = await _self_user_view(user)

    await _enrich_from_auth_user(user, user_data)
    return {"user": user_data, "message": "User retrieved successfully"}


@endpoint("/auth/ws-ticket", methods=["POST"], auth=True, tags=["Auth"])
async def mint_websocket_ticket(request: Request) -> Dict[str, Any]:
    """Mint a short-lived, single-use ticket for WebSocket authentication.

    Clients should pass ``?ticket=`` instead of embedding the JWT in the
    WebSocket URL query string (reduces token leakage via logs/history).
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    from app.services.ws_ticket import TTL_SECONDS, mint_ws_ticket

    return {
        "ticket": mint_ws_ticket(user_id),
        "expires_in": TTL_SECONDS,
    }


@endpoint("/auth/update-profile", methods=["PUT"], auth=True, tags=["Auth"])
async def update_profile(
    request: Request,
    display_name: Optional[str] = None,
    avatar_url: Optional[str] = None,
    preferences: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Update the current user's profile."""
    uid = resolve_principal_id(request)
    if not uid:
        raise MissingAuthenticationError(message="Authentication required")
    user = await get_user_node(uid)
    if not user:
        raise ResourceNotFoundError(message="User not found")

    prior_snapshot = await export_node(user)  # D-03 before-snapshot

    if display_name is not None:
        user.display_name = display_name
    if avatar_url is not None:
        user.avatar_url = avatar_url
    if preferences is not None:
        user.preferences = _merge_user_preferences(user.preferences, preferences)

    user.updated_at = utc_now_iso()
    await user.save()

    user_data = await _self_user_view(user)

    await _enrich_from_auth_user(user, user_data)

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=uid,
        action="user.update",
        resource_type="User",
        resource_id=user.id,
        before=prior_snapshot,
        after=await export_node(user),
        scope=f"user:{user.id}",
    )

    return {"user": user_data, "message": "Profile updated successfully"}


@endpoint("/auth/signup", methods=["POST"], auth=False, tags=["Auth"])
async def register_user(request: Request) -> Dict[str, Any]:
    """Register a new user with extended profile information.

    Replaces jvspatial's built-in /auth/register (removed at boot): supports
    a display name and optional organisation creation, provisions the User
    node / Personal Workspace / verification OTP / per-user context App, then
    immediately logs the user in and returns a JWT token.
    """
    try:
        body = ExtendedUserCreate(**(await request.json()))
    except ValidationError as e:
        raise BadRequestError(
            message="Validation failed for signup body",
            details={"errors": e.errors()},
        )

    auth_service = _get_auth_service()

    try:
        user_response = await auth_service.register_user(
            UserCreate(email=body.email, password=body.password)
        )
    except ValueError as e:
        raise BadRequestError(message=str(e))

    auth_user_id = user_response.id

    user_node = await User.create(
        user_id=auth_user_id,
        display_name=body.name,
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
    )
    with contextlib.suppress(Exception):
        await catalog_user(user_node)

    # Send a verification OTP. Non-blocking: email failure must not break signup.
    with contextlib.suppress(Exception):
        await create_verification_request(user_node, body.email)

    # Every authenticated user starts with a Personal Workspace — the
    # default scope for their private Apps/Tracks/Entries/threads.
    from app.services.personal_workspace import ensure_personal_workspace

    personal_workspace = None
    try:
        personal_workspace = await ensure_personal_workspace(user_node)
    except Exception:
        logger.exception("ensure_personal_workspace failed during signup")

    # The App that pays attention to the person lives in that workspace and
    # is provisioned with it, so the user's first turn is already observed.
    # Idempotent, and it swallows its own failures — signup must not fail
    # because an App did not install.
    if personal_workspace is not None:
        from app.services.personal_context import provision_personal_context_app

        with contextlib.suppress(Exception):
            await provision_personal_context_app(user_id=user_node.id)

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    # actor_kind="system" because /auth/signup runs unauthenticated (auth=False)
    # — the new User itself is the resource; the authenticating principal does
    # not exist yet at this point.
    await emit_change_event(
        actor_kind="system",
        actor_id="signup",
        action="user.create",
        resource_type="User",
        resource_id=user_node.id,
        before=None,
        after=await export_node(user_node),
        scope=f"user:{user_node.id}",
    )

    if personal_workspace is not None:
        await emit_change_event(
            actor_kind="system",
            actor_id="signup",
            action="workspace.create",
            resource_type="Workspace",
            resource_id=personal_workspace.id,
            before=None,
            after=await export_node(personal_workspace),
            scope=f"user:{user_node.id}",
        )

    workspace_data: Optional[Dict[str, Any]] = None
    if body.workspaceName and body.workspaceName.strip():
        now_iso = utc_now_iso()
        ws_name = body.workspaceName.strip()
        org_ws = await Workspace.create(
            kind="organization",
            workspace_type="company",
            name=ws_name,
            name_fold=ws_name.casefold(),
            created_at=now_iso,
            updated_at=now_iso,
        )
        await user_node.connect(
            org_ws,
            edge=IS_MEMBER_OF,
            role="owner",
            joined_at=now_iso,
            can_create_apps=True,
            can_create_tracks=True,
        )

        with contextlib.suppress(Exception):
            await catalog_workspace(org_ws)

        workspace_data = {
            "id": org_ws.id,
            "name": org_ws.name,
            "kind": "organization",
            "workspace_type": "company",
            "role": "owner",
        }

        await emit_change_event(
            actor_kind="human",
            actor_id=user_node.id,
            action="workspace.create",
            resource_type="Workspace",
            resource_id=org_ws.id,
            before=None,
            after=await export_node(org_ws),
            scope=f"user:{user_node.id}",
        )

    token_response = await auth_service.login_user(
        UserLogin(email=body.email, password=body.password)
    )

    # Scrubbed: signup runs immediately after ``create_verification_request``
    # wrote the OTP slot, so a raw export publishes the code's hash.
    user_data = await _self_user_view(user_node)
    await _enrich_from_auth_user(user_node, user_data)
    user_data["name"] = body.name
    user_data["email"] = body.email

    response: Dict[str, Any] = {
        "access_token": token_response.access_token,
        "token_type": token_response.token_type,
        "expires_in": token_response.expires_in,
        "refresh_token": token_response.refresh_token,
        "refresh_expires_in": token_response.refresh_expires_in,
        "user": user_data,
    }
    if workspace_data:
        response["workspace"] = workspace_data

    return response


# ─────────────────────────────────────────────────────────────────────────────
# Email verification
# ─────────────────────────────────────────────────────────────────────────────


@endpoint("/auth/verify-email", methods=["POST"], auth=True, tags=["Auth"])
async def verify_email(request: Request) -> Dict[str, Any]:
    """Consume a 6-digit verification OTP and mark the account as verified.

    Non-blocking: login is not gated on ``email_verified``. The client calls
    this after the user enters the code on the verification screen.
    """
    try:
        body = VerifyEmailRequest(**(await request.json()))
    except ValidationError as e:
        raise BadRequestError(
            message="Validation failed",
            details={"errors": e.errors()},
        )

    uid = resolve_principal_id(request)
    if not uid:
        raise MissingAuthenticationError(message="Authentication required")
    user = await get_user_node(uid)
    if not user:
        raise ResourceNotFoundError(message="User not found")

    ok, error_code = await consume_verification_code(user, body.code)
    if not ok:
        if error_code == ERR_ALREADY_VERIFIED:
            return {"ok": True, "message": "Email already verified"}
        if error_code == ERR_EXPIRED_CODE:
            raise BadRequestError(
                message="Verification code has expired. Please request a new one.",
                details={"error_code": error_code},
            )
        if error_code == ERR_ATTEMPTS_EXCEEDED:
            raise BadRequestError(
                message="Too many incorrect attempts. Please request a new code.",
                details={"error_code": error_code},
            )
        raise BadRequestError(
            message="Invalid verification code.",
            details={"error_code": error_code},
        )

    return {"ok": True, "message": "Email verified successfully"}


@endpoint("/auth/resend-verification", methods=["POST"], auth=True, tags=["Auth"])
async def resend_verification(request: Request) -> Dict[str, Any]:
    """Generate and send a fresh verification OTP to the authenticated user's email."""
    uid = resolve_principal_id(request)
    if not uid:
        raise MissingAuthenticationError(message="Authentication required")
    user = await get_user_node(uid)
    if not user:
        raise ResourceNotFoundError(message="User not found")

    if user.email_verified:
        return {"ok": True, "message": "Email already verified"}

    auth_user = await AuthUser.get(user.user_id)
    if not auth_user:
        raise ResourceNotFoundError(message="Auth user not found")

    email = getattr(auth_user, "email", "") or ""
    if not email:
        raise BadRequestError(message="No email address on file")

    # Rate-limit: if an unexpired code is already pending, don't generate a
    # new one — the existing code is still valid and the user should wait.
    if has_pending_code(user):
        return {
            "ok": True,
            "message": "A code is already pending. Please check your email.",
        }

    with contextlib.suppress(Exception):
        await create_verification_request(user, email)

    return {"ok": True, "message": "Verification code sent"}


# ─────────────────────────────────────────────────────────────────────────────
# Password reset
# ─────────────────────────────────────────────────────────────────────────────


@endpoint("/auth/forgot-password", methods=["POST"], auth=False, tags=["Auth"])
async def forgot_password(request: Request) -> Dict[str, Any]:
    """Trigger a password reset email for ``email``.

    Always returns 200 with a generic success message regardless of whether
    the email is registered — this prevents account enumeration via the
    reset endpoint. Operators can find the actual email destination in
    application logs (or via the email provider dashboard in production).

    Raises 422 for malformed input (e.g. invalid email format) so the
    client knows the request body itself was structurally invalid, not
    that the account was not found.
    """
    try:
        body = ForgotPasswordRequest(**(await request.json()))
    except ValidationError as exc:
        raise UnprocessableEntityError(
            message="Invalid request: "
            + "; ".join(e.get("msg", "validation error") for e in exc.errors()),
        ) from exc

    # Local import keeps the agentive layer honest about its conditional
    # loading — password reset is core, but matching the import-where-used
    # pattern keeps cold-import surface small.
    from app.services.password_reset import create_reset_request

    try:
        await create_reset_request(body.email)
    except Exception:
        # Never propagate — same response either way (anti-enumeration).
        logger.exception(
            "forgot_password: unexpected failure for email=%s; returning generic OK",
            body.email,
        )

    return {
        "ok": True,
        "message": (
            "If an account exists for that email, a reset link has been sent. "
            "Check your inbox (and spam folder)."
        ),
    }


@endpoint("/auth/reset-password", methods=["POST"], auth=False, tags=["Auth"])
async def reset_password(request: Request) -> Dict[str, Any]:
    """Consume a reset token and set a new password.

    Surfaces canonical ``error_code`` strings so the frontend can map them
    to specific UI states (e.g. expired link → "request a new link").

    Raises 422 for missing or structurally invalid fields, and 400 with a
    specific ``error_code`` in the ``detail`` key for domain errors such as
    invalid/expired tokens or a password that does not meet the minimum
    length requirement.
    """
    try:
        body = ResetPasswordRequest(**(await request.json()))
    except ValidationError as exc:
        raise UnprocessableEntityError(
            message="Invalid request: "
            + "; ".join(e.get("msg", "validation error") for e in exc.errors()),
        ) from exc

    from app.services.password_reset import consume_reset_token

    ok, error_code = await consume_reset_token(body.token, body.password)
    if not ok:
        # 400 for client-fixable errors (bad/expired token, weak password).
        raise PasswordResetError(
            message=_reset_error_message(error_code),
            reset_error_code=error_code or "auth.reset.unknown",
        )

    return {"ok": True, "message": "Password updated. You can now sign in."}


def _reset_error_message(error_code: Optional[str]) -> str:
    """Map error_code → user-readable message. Frontend may override."""
    from app.services.password_reset import (
        ERR_ATTEMPTS_EXCEEDED,
        ERR_EXPIRED_TOKEN,
        ERR_INVALID_TOKEN,
        ERR_USER_INACTIVE,
        ERR_WEAK_PASSWORD,
    )

    return {
        ERR_INVALID_TOKEN: "This reset link is invalid. Please request a new one.",
        ERR_EXPIRED_TOKEN: "This reset link has expired. Please request a new one.",
        ERR_ATTEMPTS_EXCEEDED: ("Too many attempts. Please request a new reset link."),
        ERR_WEAK_PASSWORD: (
            f"Password must be at least {settings.PASSWORD_MIN_LENGTH} characters."
        ),
        ERR_USER_INACTIVE: "This account is inactive. Contact support.",
    }.get(error_code or "", "Unable to reset password. Please try again.")
