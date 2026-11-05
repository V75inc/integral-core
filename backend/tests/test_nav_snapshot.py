"""Navigation snapshots: a short cache for LISTING endpoints, off by default.

``get_user_accessible_tracks`` and ``get_user_accessible_apps`` run as live-permission
boundaries, so the process TTL cache never serves them. Listing endpoints can opt in to
a snapshot (``INTEGRAL_NAV_SNAPSHOT_TTL``). The snapshot must never outlive an
invalidation, never be stored by a computation that raced one, and concurrent callers
must share one computation.
"""

import asyncio

import pytest

from app.services import permissions, permissions_process_cache as ppc


@pytest.fixture
def snapshots_on(monkeypatch):
    monkeypatch.setattr(ppc, "_NAV_ENABLED", True)
    monkeypatch.setattr(ppc, "_NAV_TTL_SECONDS", 30.0)
    ppc._nav_store.clear()
    ppc._nav_inflight.clear()
    yield
    ppc._nav_store.clear()
    ppc._nav_inflight.clear()


def _counting(monkeypatch, name, result, delay=0.0):
    calls = {"n": 0}

    async def fake(user_id):
        calls["n"] += 1
        if delay:
            await asyncio.sleep(delay)
        return result

    monkeypatch.setattr(permissions, name, fake)
    return calls


@pytest.mark.asyncio
async def test_disabled_by_default_calls_through_every_time(monkeypatch):
    monkeypatch.setattr(ppc, "_NAV_ENABLED", False)
    calls = _counting(monkeypatch, "get_user_accessible_tracks", ["t1"])
    assert await permissions.get_user_accessible_tracks_for_navigation("u") == ["t1"]
    assert await permissions.get_user_accessible_tracks_for_navigation("u") == ["t1"]
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_second_call_is_served_from_the_snapshot(snapshots_on, monkeypatch):
    calls = _counting(monkeypatch, "get_user_accessible_tracks", ["t1", "t2"])
    first = await permissions.get_user_accessible_tracks_for_navigation("u")
    second = await permissions.get_user_accessible_tracks_for_navigation("u")
    assert first == second == ["t1", "t2"]
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_apps_use_their_own_snapshot_key(snapshots_on, monkeypatch):
    tracks = _counting(monkeypatch, "get_user_accessible_tracks", ["t"])
    apps = _counting(monkeypatch, "get_user_accessible_apps", ["a"])
    assert await permissions.get_user_accessible_tracks_for_navigation("u") == ["t"]
    assert await permissions.get_user_accessible_apps_for_navigation("u") == ["a"]
    assert await permissions.get_user_accessible_apps_for_navigation("u") == ["a"]
    assert (tracks["n"], apps["n"]) == (1, 1)


@pytest.mark.asyncio
async def test_invalidate_user_drops_the_snapshot(snapshots_on, monkeypatch):
    calls = _counting(monkeypatch, "get_user_accessible_tracks", ["t1"])
    await permissions.get_user_accessible_tracks_for_navigation("u")
    ppc.invalidate_user("u")
    await permissions.get_user_accessible_tracks_for_navigation("u")
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_other_users_are_not_affected_by_an_invalidation(snapshots_on, monkeypatch):
    calls = _counting(monkeypatch, "get_user_accessible_tracks", ["t1"])
    await permissions.get_user_accessible_tracks_for_navigation("a")
    await permissions.get_user_accessible_tracks_for_navigation("b")
    ppc.invalidate_user("a")
    await permissions.get_user_accessible_tracks_for_navigation("b")
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_a_computation_that_raced_an_invalidation_is_not_stored(snapshots_on, monkeypatch):
    calls = {"n": 0}

    async def slow(user_id):
        calls["n"] += 1
        await asyncio.sleep(0.05)
        return ["stale"]

    monkeypatch.setattr(permissions, "get_user_accessible_tracks", slow)
    task = asyncio.ensure_future(permissions.get_user_accessible_tracks_for_navigation("u"))
    await asyncio.sleep(0.01)
    ppc.invalidate_user("u")  # a grant or revoke lands while the inventory is computed
    await task
    await permissions.get_user_accessible_tracks_for_navigation("u")
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_concurrent_callers_share_one_computation(snapshots_on, monkeypatch):
    calls = _counting(monkeypatch, "get_user_accessible_tracks", ["t1"], delay=0.05)
    results = await asyncio.gather(
        *(permissions.get_user_accessible_tracks_for_navigation("u") for _ in range(5))
    )
    assert results == [["t1"]] * 5
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_snapshot_expires(snapshots_on, monkeypatch):
    monkeypatch.setattr(ppc, "_NAV_TTL_SECONDS", 0.05)
    calls = _counting(monkeypatch, "get_user_accessible_tracks", ["t1"])
    await permissions.get_user_accessible_tracks_for_navigation("u")
    await asyncio.sleep(0.08)
    await permissions.get_user_accessible_tracks_for_navigation("u")
    assert calls["n"] == 2


@pytest.fixture
def stale_window(monkeypatch):
    monkeypatch.setattr(ppc, "_NAV_STALE_SECONDS", 60.0)
    yield


@pytest.mark.asyncio
async def test_expired_snapshot_is_served_while_it_refreshes(snapshots_on, stale_window, monkeypatch):
    monkeypatch.setattr(ppc, "_NAV_TTL_SECONDS", 0.05)
    values = [["old"], ["new"]]
    calls = {"n": 0}

    async def fake(user_id):
        calls["n"] += 1
        value = values[min(calls["n"] - 1, 1)]
        if calls["n"] > 1:
            await asyncio.sleep(0.05)
        return value

    monkeypatch.setattr(permissions, "get_user_accessible_tracks", fake)
    assert await permissions.get_user_accessible_tracks_for_navigation("u") == ["old"]
    await asyncio.sleep(0.08)  # expired, still inside the stale window
    started = asyncio.get_event_loop().time()
    assert await permissions.get_user_accessible_tracks_for_navigation("u") == ["old"]
    assert asyncio.get_event_loop().time() - started < 0.03  # did not wait for the refresh
    await asyncio.sleep(0.15)  # the background refresh finishes
    assert await permissions.get_user_accessible_tracks_for_navigation("u") == ["new"]
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_invalidation_removes_the_stale_copy_too(snapshots_on, stale_window, monkeypatch):
    monkeypatch.setattr(ppc, "_NAV_TTL_SECONDS", 0.05)
    calls = _counting(monkeypatch, "get_user_accessible_tracks", ["t1"])
    await permissions.get_user_accessible_tracks_for_navigation("u")
    await asyncio.sleep(0.08)
    ppc.invalidate_user("u")  # a revoke: the expired copy must not be served
    await permissions.get_user_accessible_tracks_for_navigation("u")
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_without_a_stale_window_an_expired_snapshot_recomputes(snapshots_on, monkeypatch):
    monkeypatch.setattr(ppc, "_NAV_TTL_SECONDS", 0.05)
    monkeypatch.setattr(ppc, "_NAV_STALE_SECONDS", 0.0)
    calls = _counting(monkeypatch, "get_user_accessible_tracks", ["t1"])
    await permissions.get_user_accessible_tracks_for_navigation("u")
    await asyncio.sleep(0.08)
    await permissions.get_user_accessible_tracks_for_navigation("u")
    assert calls["n"] == 2
