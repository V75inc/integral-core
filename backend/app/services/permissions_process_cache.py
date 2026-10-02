"""Process-level TTL cache for the user-scoped access aggregates.

The per-request cache (``middleware/permissions_cache``) only dedups within a
single request. The user-scoped aggregates — ``get_user_accessible_tracks`` /
``get_user_accessible_apps`` / ``list_accessible_workspaces`` — are otherwise
recomputed cold on every request, which is the dominant cost of the dashboard
and list endpoints (each walks ``resolve_role`` over every candidate track/app).

This module caches the *result* per user for a short TTL, and drops a user's
entry the moment a permission edge affecting them is written (the
``sharing.add_collaborator`` / ``remove_collaborator`` / ``add_exclusion`` /
``remove_exclusion`` paths call ``invalidate_user``). Because invalidation is
keyed on the AFFECTED user, a revoked user's cache is dropped synchronously with
the revoke. Per-user generations also prevent older in-flight reads from
repopulating the cache after invalidation. The TTL is a backstop bounding
staleness for any grant/revoke path not explicitly hooked (e.g. org-membership
edits, resource creation) to ``_TTL_SECONDS``.

Disabled when ``TESTING=1`` (tests grant/revoke and assert immediately, and the
cache is a pure performance layer — correctness is identical without it) and
when the TTL is set to 0.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional, Tuple

_TTL_SECONDS = float(os.getenv("PERMISSION_PROCESS_CACHE_TTL", "20"))
_ENABLED = os.getenv("TESTING", "") != "1" and _TTL_SECONDS > 0

# {user_id: {key: (expiry_monotonic, value)}}
_store: Dict[str, Dict[str, Tuple[float, Any]]] = {}
# Monotonic per-user generations fence off computations that began before a
# permission or resource write invalidated the cache. Without this, an older
# concurrent read can finish after invalidate_user() and put its stale result
# back into the cache for the full TTL.
_generations: Dict[str, int] = {}


def enabled() -> bool:
    """True when the process cache is active (not TESTING, TTL > 0)."""
    return _ENABLED


def get_cached(user_id: str, key: str) -> Optional[Any]:
    """Return the cached value for (user, key), or None on miss/expiry."""
    if not _ENABLED or not user_id:
        return None
    bucket = _store.get(user_id)
    if not bucket:
        return None
    hit = bucket.get(key)
    if hit is None:
        return None
    expiry, value = hit
    if time.monotonic() >= expiry:
        bucket.pop(key, None)
        return None
    return value


def generation(user_id: str) -> int:
    """Return the current invalidation generation for ``user_id``."""
    if not user_id:
        return 0
    return _generations.get(user_id, 0)


def set_cached(
    user_id: str,
    key: str,
    value: Any,
    *,
    expected_generation: Optional[int] = None,
) -> None:
    """Store a value unless an invalidation raced its computation.

    Callers that compute values across awaits should capture ``generation``
    before starting and pass it here. A mismatched generation means a write
    happened while the read was in flight, so its result must not be cached.
    """
    if not _ENABLED or not user_id:
        return
    if expected_generation is not None and generation(user_id) != expected_generation:
        return
    expiry = time.monotonic() + _TTL_SECONDS
    _store.setdefault(user_id, {})[key] = (expiry, value)


def invalidate_user(user_id: str) -> None:
    """Drop all cached aggregates for one user (call on any permission change
    affecting them). Safe to call when disabled or when the user has no entry.
    """
    if user_id:
        if _ENABLED:
            _generations[user_id] = generation(user_id) + 1
        _store.pop(user_id, None)


def invalidate_user_aliases(user: Any) -> None:
    """Evict a graph User and the auth principal that represents that User."""
    for principal_id in {getattr(user, "id", None), getattr(user, "user_id", None)}:
        if principal_id:
            invalidate_user(str(principal_id))


_ROLE_CACHE_MISS = object()
_NIL_ROLE = "__resolve_role_nil__"


def resolve_role_cache_key(resource_type: str, resource_id: str) -> str:
    """Process-cache key for a single ``resolve_role`` result."""
    return f"rr:{resource_type}:{resource_id}"


def get_resolve_role_cached(user_id: str, resource_type: str, resource_id: str) -> Any:
    """Return cached role, ``_ROLE_CACHE_MISS`` on miss/expiry."""
    if not _ENABLED or not user_id:
        return _ROLE_CACHE_MISS
    hit = get_cached(user_id, resolve_role_cache_key(resource_type, resource_id))
    if hit is None:
        return _ROLE_CACHE_MISS
    return None if hit == _NIL_ROLE else hit


def set_resolve_role_cached(
    user_id: str,
    resource_type: str,
    resource_id: str,
    value: Any,
    *,
    expected_generation: Optional[int] = None,
) -> None:
    """Store a resolve_role result under the module TTL."""
    stored = _NIL_ROLE if value is None else value
    set_cached(
        user_id,
        resolve_role_cache_key(resource_type, resource_id),
        stored,
        expected_generation=expected_generation,
    )


def clear_all() -> None:
    """Drop the entire cache (test helper / coarse reset)."""
    _store.clear()
    _generations.clear()
