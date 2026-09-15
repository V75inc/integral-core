"""Server-side workspace scope enforcement for list endpoints (W5).

Reads ``X-Integral-Scope`` from the request and resolves the canonical
target ``workspace_id`` against live membership. The header is never
trusted blindly, and — as of the SM3 fix — it is never silently
*discarded* either: an explicitly supplied scope either resolves to a
workspace the caller can currently reach, or the request fails. Falling
back to the caller's Personal Workspace when the client asked for some
other workspace returned somebody's own data under a scope they did not
ask for, which contradicted the documented contract ("backend validates
caller has access to that workspace and refuses cross-workspace reads")
and hid stale-workspace-id bugs in clients — a frontend pinned to a
deleted workspace looked like it was working.

Resolution priority:

1. ``X-Integral-Scope: ws:<id>`` header. The caller MUST currently
   belong to that workspace; otherwise the resolver raises
   ``InsufficientPermissionsError`` (403). A header that is present but
   not in canonical ``ws:<id>`` form raises ``BadRequestError`` (400) —
   ``parse_scope_header`` returns ``None`` for a bare id, and silently
   treating that as "no scope" was the same masking bug in another
   costume.
2. ``User.active_workspace_id`` — the server-persisted last-good scope.
   Consulted ONLY for ``skip_header=True`` callers (``GET
   /users/me/scope``), which are the authority on the stored preference.
3. The user's Personal Workspace (auto-created on first read). This is a
   fail-closed default for header-*less* list requests, which stays
   deliberate: omitting the header is a valid "give me my default scope"
   request, unlike naming a workspace the caller cannot see.

403 is used for both "unknown workspace id" and "no access", rather than
splitting 404/403, so the response cannot be used to enumerate which
workspace ids exist.

Side effect: a validated header scope is persisted to
``User.active_workspace_id`` so the server side stays authoritative
across devices and across client-cache flushes.

Usage:

    from app.services.request_scope import resolve_workspace_id_from_request

    workspace_id = await resolve_workspace_id_from_request(request, user_id)
    if workspace_id is not None:
        items = [it for it in items if matches_workspace(it, workspace_id)]
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _effective_workspace_id_of(item: Any) -> Optional[str]:
    """Mirrors ``tool_scope._effective_workspace_id`` for non-agent paths."""
    direct = getattr(item, "workspace_id", None)
    if direct:
        return str(direct)
    app = getattr(item, "app", None)
    if app is not None:
        inh = getattr(app, "workspace_id", None)
        if inh:
            return str(inh)
    if isinstance(item, dict):
        d = item.get("workspace_id")
        if d:
            return str(d)
        sp = item.get("app")
        if isinstance(sp, dict):
            inh = sp.get("workspace_id")
            if inh:
                return str(inh)
    return None


def matches_workspace(item: Any, workspace_id: str) -> bool:
    """True iff the item's effective workspace_id equals the target."""
    return _effective_workspace_id_of(item) == workspace_id


async def _persist_active_workspace(
    user_id: str, workspace_id: str, *, explicit: bool
) -> None:
    """Best-effort write of ``User.active_workspace_id``. Logs + swallows
    storage failures because every list endpoint funnels through here and
    we never want a write hiccup to turn into a 500 on read paths.

    ``explicit=True`` marks the write as a user-driven preference (header
    matched live membership, or a ``PUT /users/me/scope`` call). Auto-
    default picks (most-recent org / Personal fallback) pass
    ``explicit=False`` — these get persisted for caching/observability
    but the resolver re-evaluates them on the next request, so a user
    who later gains org membership flips to the org automatically.
    """
    if not workspace_id:
        return
    try:
        from app.services.permissions import get_user_node

        user = await get_user_node(user_id)
        if user is None:
            return
        current = str(getattr(user, "active_workspace_id", "") or "")
        current_explicit = bool(getattr(user, "active_workspace_id_explicit", False))
        if current == workspace_id and current_explicit == explicit:
            return
        user.active_workspace_id = workspace_id
        user.active_workspace_id_explicit = explicit
        await user.save()
    except Exception:
        logger.warning(
            "request_scope: failed to persist active_workspace_id=%s for user=%s",
            workspace_id,
            user_id,
            exc_info=True,
        )


async def _user_member_of(user_id: str, workspace_id: str) -> bool:
    """Lightweight membership check used to validate the header hint."""
    if not workspace_id:
        return False
    try:
        from app.services.workspace_permissions import (
            user_in_workspace_member_pool,
        )

        return await user_in_workspace_member_pool(user_id, workspace_id)
    except Exception:
        logger.exception(
            "request_scope: membership check raised for user=%s ws=%s",
            user_id,
            workspace_id,
        )
        return False


async def _pick_default_workspace(user_id: str) -> Optional[str]:
    """Return the user's Personal Workspace as the fail-closed default.

    When no ``X-Integral-Scope`` header is present and the user has no
    explicit stored preference, the resolver defaults to the caller's
    Personal Workspace — never to an org workspace the user happens to
    belong to. This is the "fail-closed" contract documented at W5: a
    missing scope header must never silently expose org data.

    Callers that want the most-recent org workspace as the default can
    send the ``X-Integral-Scope`` header explicitly; doing so promotes the
    preference to ``explicit=True`` via step 1 of the resolver, where it
    will persist across subsequent header-less requests.
    """
    from app.services.personal_workspace import (
        ensure_personal_workspace_for_user_id,
    )

    try:
        ws = await ensure_personal_workspace_for_user_id(user_id)
        return ws.id if ws else None
    except Exception:
        logger.exception("personal workspace fallback failed for %s", user_id)
        return None


async def resolve_workspace_id_from_request(
    request, user_id: str, *, skip_header: bool = False
) -> Optional[str]:
    """Return the workspace_id the caller is currently operating within.

    See module docstring for the full resolution priority. The returned
    id is guaranteed to be one the user has live access to, never just
    "whatever the client sent."

    Raises ``BadRequestError`` (400) when ``X-Integral-Scope`` is present
    but not in canonical ``ws:<id>`` form, and 403
    (``InsufficientPermissionsError``) when it names a workspace the
    caller cannot reach — unknown id and revoked membership are not
    distinguished. An absent header is not an error; it resolves to the
    caller's Personal Workspace.

    ``skip_header=True`` bypasses step 1 (the ``X-Integral-Scope`` hint)
    entirely. Use this for endpoints that ARE the authoritative source of
    the user's scope — specifically ``GET /users/me/scope``. Without the
    skip, a stale localStorage value sent as the header would validate
    against live membership (always passes for the user's own Personal
    workspace), get persisted as ``explicit=True``, and overwrite whatever
    correct preference the server had already stored.
    """
    from app.api.errors import BadRequestError, InsufficientPermissionsError
    from app.middleware.agentive_scope import set_scope_key
    from app.services.permissions import get_user_node
    from app.services.scope_header import parse_scope_header

    # Per-request memoization to avoid repeated header parsing +
    # membership checks when a handler calls this helper multiple times.
    if request is not None:
        state = getattr(request, "state", None)
        cache = getattr(state, "workspace_resolution", None) if state else None
        cache_key = (str(user_id), bool(skip_header))
        if isinstance(cache, dict) and cache_key in cache:
            resolved = cache[cache_key]
            set_scope_key(resolved)
            return resolved

    # 1. Explicit header scope. The caller must actually have access;
    #    otherwise this raises rather than degrading to another workspace.
    #    Skipped when the caller is the scope-resolution endpoint itself
    #    (GET /users/me/scope) to keep the server authoritative.
    requested: Optional[str] = None
    if not skip_header:
        try:
            raw = request.headers.get("x-integral-scope") if request else None
        except Exception:
            raw = None
        scope = parse_scope_header(raw)
        if scope and scope.get("kind") == "workspace":
            requested = str(scope.get("workspace_id") or "") or None
        elif raw is not None and str(raw).strip():
            # Present but unparseable — most often a bare workspace id with
            # the mandatory ``ws:`` prefix missing. Treating it as absent
            # silently served the Personal Workspace instead.
            raise BadRequestError(
                message=(
                    "Invalid X-Integral-Scope header; expected 'ws:<workspace_id>'"
                ),
            )

    if requested:
        if not await _user_member_of(user_id, requested):
            # Unknown id and revoked/absent membership are deliberately
            # indistinguishable so the header cannot enumerate workspaces.
            raise InsufficientPermissionsError(
                message="No access to the requested workspace scope",
            )
        # Header scope is good. The frontend sends a header whenever the
        # user has explicitly chosen a workspace, so promote this write
        # to ``explicit=True``.
        await _persist_active_workspace(user_id, requested, explicit=True)
        # Populate the agentive scope contextvar so deep agentive call
        # paths (e.g. the embedded agent action's request scoping) can read
        # the workspace_id without explicit parameter plumbing.
        set_scope_key(requested)
        if request is not None and getattr(request, "state", None) is not None:
            cache = getattr(request.state, "workspace_resolution", None)
            if not isinstance(cache, dict):
                cache = {}
                request.state.workspace_resolution = cache
            cache[(str(user_id), bool(skip_header))] = requested
        return requested

    # 2. Server-persisted EXPLICIT preference — consulted ONLY when
    # ``skip_header=True``, because that caller IS the authoritative scope
    # reader (GET /users/me/scope). An invalid header no longer reaches this
    # point: step 1 raises instead of falling through.
    #
    # For list endpoints (skip_header=False): a missing header means the
    # caller is making an unscoped request that must fail-closed to Personal
    # Workspace — we deliberately don't let a stored preference bleed into
    # header-less requests, as that would silently expose org data to a new
    # browser tab or a fresh API call that omits the header.
    #
    # For GET /users/me/scope (skip_header=True): we MUST consult the stored
    # value, because that endpoint's whole job is to return the user's last
    # explicit choice so the FE can hydrate the workspace switcher. Without
    # this branch the endpoint always fell through to the Personal default
    # and overwrote the stored value (explicit=False) on every page refresh.
    if skip_header:
        try:
            user = await get_user_node(user_id)
        except Exception:
            user = None
        stored = str(getattr(user, "active_workspace_id", "") or "") if user else ""
        stored_explicit = bool(getattr(user, "active_workspace_id_explicit", False))
        if stored and stored_explicit and await _user_member_of(user_id, stored):
            set_scope_key(stored)
            if request is not None and getattr(request, "state", None) is not None:
                cache = getattr(request.state, "workspace_resolution", None)
                if not isinstance(cache, dict):
                    cache = {}
                    request.state.workspace_resolution = cache
                cache[(str(user_id), bool(skip_header))] = stored
            return stored

    # 3. Fail-closed default: the user's Personal Workspace. Persisted with
    # explicit=False so a future request that supplies a valid header still
    # overrides it cleanly via step 1.
    default_id = await _pick_default_workspace(user_id)
    if default_id:
        await _persist_active_workspace(user_id, default_id, explicit=False)
    set_scope_key(default_id)
    if request is not None and getattr(request, "state", None) is not None:
        cache = getattr(request.state, "workspace_resolution", None)
        if not isinstance(cache, dict):
            cache = {}
            request.state.workspace_resolution = cache
        cache[(str(user_id), bool(skip_header))] = default_id
    return default_id
