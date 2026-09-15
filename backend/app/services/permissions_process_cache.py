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
the revoke — there is no stale-access window for the changed user. The TTL is a
backstop bounding staleness for any grant/revoke path not explicitly hooked
(e.g. org-membership edits, resource creation) to ``_TTL_SECONDS``.

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


def set_cached(user_id: str, key: str, value: Any) -> None:
    """Store a value for (user, key) with the module TTL."""
    if not _ENABLED or not user_id:
        return
    _store.setdefault(user_id, {})[key] = (time.monotonic() + _TTL_SECONDS, value)


def invalidate_user(user_id: str) -> None:
    """Drop all cached aggregates for one user (call on any permission change
    affecting them). Safe to call when disabled or when the user has no entry.
    """
    if user_id:
        _store.pop(user_id, None)


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
) -> None:
    """Store a resolve_role result under the module TTL."""
    stored = _NIL_ROLE if value is None else value
    set_cached(user_id, resolve_role_cache_key(resource_type, resource_id), stored)


def clear_all() -> None:
    """Drop the entire cache (test helper / coarse reset)."""
    _store.clear()
