"""Shared API utilities for node export and context flattening."""

from typing import Any, Dict, List, Optional

from starlette.requests import Request


def _admit_desktop_principal(principal: str) -> str:
    from app.runtime.composition import admits_human

    if not admits_human(principal):
        from app.api.errors import MissingAuthenticationError

        raise MissingAuthenticationError(message="The local owner session is required")
    return principal


def resolve_principal_id(
    request: Optional[Request],
    *,
    user_id: Optional[str] = None,
    current_user_id: Optional[str] = None,
) -> Optional[str]:
    """Resolve the authenticated principal id.

    Prefer values injected by jvspatial; fall back to ``request.state.user`` when
    injection is missing (e.g. some GET registrations).
    """
    if user_id:
        return _admit_desktop_principal(user_id)
    if current_user_id:
        return _admit_desktop_principal(current_user_id)
    return get_user_id_from_request(request)


def get_user_id_from_request(request: Optional[Request]) -> Optional[str]:
    """Extract user_id from request.state.user (set by auth middleware)."""
    if not request or not hasattr(request, "state"):
        return None
    user = getattr(request.state, "user", None)
    if not user:
        return None
    if hasattr(user, "id"):
        return _admit_desktop_principal(str(user.id))
    if hasattr(user, "user_id"):
        return _admit_desktop_principal(str(user.user_id))
    if isinstance(user, dict):
        return _admit_desktop_principal(
            str(user.get("id") or user.get("user_id") or "")
        )
    return None


def is_platform_admin(request: Optional[Request]) -> bool:
    """True when the JWT principal carries the platform ``admin`` role."""
    if not request or not hasattr(request, "state"):
        return False
    user: Any = getattr(request.state, "user", None)
    if user is None:
        return False
    roles = getattr(user, "roles", None)
    if not roles and isinstance(user, dict):
        roles = user.get("roles")
    return "admin" in (roles or [])


async def current_platform_admin(request: Optional[Request]) -> bool:
    """Require the current primary active identity's exact privileged roles."""
    from jvspatial.core.context import get_default_context

    from app.exceptions import AuthorizationUnavailableError
    from app.services.authority_facts import primary_auth_record
    from app.services.permissions import get_user_node

    principal_id = resolve_principal_id(request)
    if not principal_id:
        return False
    auth_id = principal_id
    if not auth_id.startswith("o.User."):
        profile = await get_user_node(principal_id)
        if profile is None or not profile.user_id:
            return False
        auth_id = profile.user_id
    row = await primary_auth_record(get_default_context().database, auth_id)
    if row is None or row["context"]["is_active"] is not True:
        return False
    roles = row["context"].get("roles")
    if not isinstance(roles, list) or any(type(role) is not str for role in roles):
        raise AuthorizationUnavailableError()
    if "roles" in row and row["roles"] != roles:
        raise AuthorizationUnavailableError()
    return "admin" in roles


async def require_platform_admin(request: Optional[Request]) -> None:
    """Raise InsufficientPermissionsError unless the caller is a platform admin."""
    from app.api.errors import InsufficientPermissionsError

    if not await current_platform_admin(request):
        raise InsufficientPermissionsError(message="admin-only endpoint")


async def export_node(node) -> dict:
    """Export a graph node with fields at the top level (``id``, ``entity``, …).

    Uses ``flat=True`` so model fields like ``name`` are not stuck only under
    ``context`` (avoids clients missing labels when they read top-level keys).

    NOTE: this emits **every** declared model field. Never hand it a user
    record destined for a client response — use :func:`public_user_view`.
    """
    return await node.export(flat=True)


# Allowlist for user objects embedded in API responses. This is deliberately an
# allowlist, not a denylist: a denylist silently starts leaking the moment a
# model gains a field, and both user records here are outside our control (the
# graph ``User`` node grows product fields; jvspatial's ``AuthUser`` carries
# ``password_hash``). These are exactly the keys the frontend reads off
# ``author?.*`` — see ``frontend/src`` (display_name, avatar_url,
# avatar_attachment_id, id, updated_at).
PUBLIC_USER_FIELDS = (
    "id",
    "entity",
    "display_name",
    "avatar_url",
    "avatar_attachment_id",
    "updated_at",
)


async def public_user_view(user) -> Dict[str, Any]:
    """Project a user record down to the fields safe to show another user.

    ``author_id`` / ``owner_user_id`` may hold either the graph ``User`` node id
    (``n.User.*``) or the jvspatial ``AuthUser`` principal id (``o.User.*``), and
    both classes persist under the ``"User"`` discriminator — so a resolver can
    hand back either type. Exporting them raw leaked ``password_hash`` /
    ``roles`` / ``permissions`` / ``email`` (AuthUser) and
    ``preferences.reset_token`` / ``notification_preferences…phone_e164``
    (graph User) into entry, comment, mission-control and **unauthenticated**
    public-share responses. Projecting by allowlist is type-agnostic, so it
    holds regardless of which record the resolver returns.

    ``display_name`` falls back to the AuthUser's ``name`` so the UI still has a
    label when the id form resolved to the auth record.
    """
    exported = await user.export(flat=True)
    view: Dict[str, Any] = {
        key: exported[key] for key in PUBLIC_USER_FIELDS if key in exported
    }
    if not view.get("display_name"):
        # AuthUser spells it ``name``; keep the UI's contract on one key.
        fallback = exported.get("name") or ""
        if fallback:
            view["display_name"] = fallback
    return view


async def attach_author_exports(
    items: List[Dict[str, Any]],
    *,
    id_field: str = "author_id",
    author_field: str = "author",
) -> None:
    """Attach projected User dicts for principal-id author fields (in-place)."""
    from app.services.permissions import batch_resolve_users_by_principal_ids

    principal_ids = [str(item[id_field]) for item in items if item.get(id_field)]
    if not principal_ids:
        return
    users_by_principal = await batch_resolve_users_by_principal_ids(principal_ids)
    for item in items:
        pid = item.get(id_field)
        if not pid:
            continue
        user = users_by_principal.get(str(pid))
        if user:
            item[author_field] = await public_user_view(user)


def platform_admin_admission(function):
    """Keep current platform role admission through an administrative effect/read.

    Target identities are admitted as inactive cleanup subjects, while the
    executing administrator must stay active. Lock order matches collaboration
    writers; cached JWT roles never establish operational authority.
    """
    import inspect
    from contextlib import nullcontext
    from functools import wraps

    signature = inspect.signature(function)

    @wraps(function)
    async def invoke(*args: Any, **kwargs: Any):
        from jvspatial.core.context import get_default_context

        from app.services.app_operations.transaction_scope import (
            graph_transaction_available,
            postgres_graph_transaction,
        )
        from app.services.identity_admission import identity_admission

        values = signature.bind(*args, **kwargs)
        request = values.arguments["request"]
        actor = resolve_principal_id(request)
        if not actor:
            await require_platform_admin(request)
        participants = tuple(
            dict.fromkeys(
                value for value in (actor, values.arguments.get("user_id")) if value
            )
        )
        database = get_default_context().database
        transaction = (
            postgres_graph_transaction(database)
            if graph_transaction_available(database)
            else nullcontext()
        )
        async with (
            transaction,
            identity_admission(participants, active_principal_ids=(actor,)),
        ):
            await require_platform_admin(request)
            result = await function(*args, **kwargs)
            await require_platform_admin(request)
            return result

    return invoke
