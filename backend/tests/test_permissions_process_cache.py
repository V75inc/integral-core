"""Unit tests for the process-level access-aggregate cache.

The cache is disabled under TESTING=1 (it is a pure performance layer), so these
tests force it on via monkeypatch to exercise the get/set/invalidate/TTL logic.
"""

import time

import app.services.permissions_process_cache as ppc


def test_get_set_invalidate(monkeypatch):
    """get/set round-trips, and invalidate_user drops only that user's entry."""
    monkeypatch.setattr(ppc, "_ENABLED", True)
    ppc.clear_all()
    assert ppc.get_cached("u1", "accessible_tracks") is None
    ppc.set_cached("u1", "accessible_tracks", [1, 2, 3])
    assert ppc.get_cached("u1", "accessible_tracks") == [1, 2, 3]
    # A different user is unaffected.
    ppc.set_cached("u2", "accessible_tracks", [9])
    ppc.invalidate_user("u1")
    assert ppc.get_cached("u1", "accessible_tracks") is None
    assert ppc.get_cached("u2", "accessible_tracks") == [9]


def test_ttl_expiry(monkeypatch):
    """A cached value disappears after the TTL elapses."""
    monkeypatch.setattr(ppc, "_ENABLED", True)
    monkeypatch.setattr(ppc, "_TTL_SECONDS", 0.05)
    ppc.clear_all()
    ppc.set_cached("u3", "accessible_apps", "v")
    assert ppc.get_cached("u3", "accessible_apps") == "v"
    time.sleep(0.07)
    assert ppc.get_cached("u3", "accessible_apps") is None


def test_disabled_is_noop(monkeypatch):
    """When disabled, set/get are no-ops and invalidate never raises."""
    monkeypatch.setattr(ppc, "_ENABLED", False)
    ppc.clear_all()
    ppc.set_cached("u4", "accessible_tracks", "v")
    assert ppc.get_cached("u4", "accessible_tracks") is None
    # invalidate / clear must not raise when disabled or empty.
    ppc.invalidate_user("u4")
    ppc.invalidate_user("")


def test_resolve_role_cache_round_trip(monkeypatch):
    """Per-resource resolve_role results cache including explicit None."""
    monkeypatch.setattr(ppc, "_ENABLED", True)
    ppc.clear_all()
    assert ppc.get_resolve_role_cached("u5", "track", "t1") is ppc._ROLE_CACHE_MISS
    ppc.set_resolve_role_cached("u5", "track", "t1", "editor")
    assert ppc.get_resolve_role_cached("u5", "track", "t1") == "editor"
    ppc.set_resolve_role_cached("u5", "track", "t2", None)
    assert ppc.get_resolve_role_cached("u5", "track", "t2") is None
    ppc.invalidate_user("u5")
    assert ppc.get_resolve_role_cached("u5", "track", "t1") is ppc._ROLE_CACHE_MISS


def test_invalidate_user_accessible_caches_clears_both_layers(monkeypatch):
    """June 29 QA #2: after a mid-batch app/track create, the acting user's
    accessible-tracks/apps must be dropped from BOTH the per-request memo AND
    the process TTL cache so the next scope check recomputes against live graph
    state (else the just-created track reads as 'outside the workspace')."""
    import app.services.permissions as permissions_mod
    from app.middleware.permissions_cache import (
        permissions_cache_get,
        reset_permissions_cache,
    )
    from app.services.permissions import invalidate_user_accessible_caches

    monkeypatch.setattr(ppc, "_ENABLED", True)
    # The per-request memo is disabled under TESTING; force it on so the helper
    # exercises the per-request-pop branch it takes in real runtime.
    monkeypatch.setattr(permissions_mod, "_permission_memo_enabled", lambda: True)
    ppc.clear_all()
    reset_permissions_cache()

    # Seed both layers with a STALE list that predates a just-created track.
    ppc.set_cached("uX", "accessible_tracks", ["stale"])
    ppc.set_cached("uX", "accessible_apps", ["stale"])
    # Per-request memo (permissions.py reads/writes these exact keys).
    per_req = permissions_cache_get()
    per_req[("accessible_tracks", "uX")] = ["stale"]
    per_req[("accessible_apps", "uX")] = ["stale"]

    invalidate_user_accessible_caches("uX")

    assert ppc.get_cached("uX", "accessible_tracks") is None
    assert ppc.get_cached("uX", "accessible_apps") is None
    per_req_after = permissions_cache_get()
    assert ("accessible_tracks", "uX") not in per_req_after
    assert ("accessible_apps", "uX") not in per_req_after
