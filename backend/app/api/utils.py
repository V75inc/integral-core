"""Shared API utilities for node export and context flattening."""

from typing import Any, Dict, List, Optional

from starlette.requests import Request


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
        return user_id
    if current_user_id:
        return current_user_id
    return get_user_id_from_request(request)


def get_user_id_from_request(request: Optional[Request]) -> Optional[str]:
    """Extract user_id from request.state.user (set by auth middleware)."""
    if not request or not hasattr(request, "state"):
        return None
    user = getattr(request.state, "user", None)
    if not user:
        return None
    if hasattr(user, "id"):
        return str(user.id)
    if hasattr(user, "user_id"):
        return str(user.user_id)
    if isinstance(user, dict):
        return str(user.get("id") or user.get("user_id") or "")
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


def require_platform_admin(request: Optional[Request]) -> None:
    """Raise InsufficientPermissionsError unless the caller is a platform admin."""
    from app.api.errors import InsufficientPermissionsError

    if not is_platform_admin(request):
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
