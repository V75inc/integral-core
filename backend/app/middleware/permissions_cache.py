"""Reset per-request permission memoization (contextvars) at HTTP request boundaries."""

from contextvars import ContextVar
from typing import Any, Dict, Optional, Tuple, Union

from starlette.types import ASGIApp, Receive, Scope, Send

# Memo keys are namespaced string tuples. Variants:
#   ("user_node", user_id)                                          — Phase 0
#   ("etr", uid, tid)                                               — Phase 0 effective track role
#   ("rr", resource_type, resource_id, user_id)                     — Phase 1 resolve_role
#   ("policy", subject_kind, subject_id, action, resource_kind, resource_id)
#                                                                   — Phase 3 D-11 policy_engine.evaluate
PermissionCacheKey = Union[
    Tuple[str, str],
    Tuple[str, str, str],
    Tuple[str, str, str, str],
    Tuple[str, str, str, str, str, str],
]

_permissions_cache_var: ContextVar[Optional[Dict[PermissionCacheKey, Any]]] = (
    ContextVar("integral_permissions_cache", default=None)
)


def reset_permissions_cache() -> None:
    """Drop the current task's permission cache (call at start of each HTTP request)."""
    _permissions_cache_var.set(None)


def permissions_cache_get() -> Dict[PermissionCacheKey, Any]:
    """Return the mutable per-request cache dict, creating it if needed."""
    cache = _permissions_cache_var.get()
    if cache is None:
        cache = {}
        _permissions_cache_var.set(cache)
    return cache


def policy_decision_clear_for_subject(subject_kind: str, subject_id: str) -> int:
    """Clear all per-request policy cache entries for a given subject (Phase 3 D-11).

    Per CONTEXT D-11: invoked by the Policy CRUD endpoints (POST / PATCH / DELETE
    /api/policies) whenever a Policy mutation occurs in the same request — the
    just-modified subject's cached ``policy_engine.evaluate`` decisions become
    stale within the request lifetime. The contextvar cache is per-request, so
    invalidation across requests is unnecessary (the next request starts with a
    fresh cache via ``reset_permissions_cache``).

    Cache keys touched have the 6-tuple shape
    ``("policy", subject_kind, subject_id, action, resource_kind, resource_id)``
    written by ``policy_engine.evaluate``. Other key shapes (Phase 0 user_node,
    Phase 0 etr, Phase 1 rr) are not touched.

    Returns the number of entries removed (for observability / test assertion).
    """
    cache = _permissions_cache_var.get()
    if cache is None:
        return 0
    to_remove = [
        k
        for k in cache.keys()
        if (
            isinstance(k, tuple)
            and len(k) == 6
            and k[0] == "policy"
            and k[1] == subject_kind
            and k[2] == subject_id
        )
    ]
    for k in to_remove:
        del cache[k]
    return len(to_remove)


class PermissionsCacheMiddleware:
    """Ensure permission helpers see a fresh cache for each incoming request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Clear the per-request permission cache for HTTP requests, then invoke the app."""
        if scope["type"] == "http":
            reset_permissions_cache()
        await self.app(scope, receive, send)
