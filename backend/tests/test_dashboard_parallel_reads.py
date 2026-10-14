"""Bound dashboard fan-out without sharing authorization decisions."""

import asyncio
from types import SimpleNamespace

import pytest

from app.middleware.permissions_cache import (
    isolated_permissions_cache,
    live_permissions_required,
    permissions_cache_get,
)
from app.services import dashboard_service as service

pytestmark = [pytest.mark.asyncio, pytest.mark.smoke]


async def test_widget_reads_are_bounded_and_preserve_order(monkeypatch):
    """Run three scoped reads at most and return declaration order."""
    active = peak = 0
    started = asyncio.Event()
    release = asyncio.Event()

    async def resolve(**kwargs):
        nonlocal active, peak
        assert kwargs["user_id"] == "user"
        assert kwargs["app_id"] == "app"
        assert kwargs["workspace_id"] == "workspace"
        active += 1
        peak = max(peak, active)
        if active == 3:
            started.set()
        try:
            await release.wait()
            return {"value": kwargs["widget"]["value"]}
        finally:
            active -= 1

    monkeypatch.setattr(service, "resolve_widget_data", resolve)
    dashboard = SimpleNamespace(
        widgets=[{"id": str(index), "value": index} for index in range(8)]
    )
    task = asyncio.create_task(
        service.resolve_dashboard_data(
            user_id="user", app_id="app", workspace_id="workspace", dashboard=dashboard
        )
    )
    await asyncio.wait_for(started.wait(), 2)
    assert peak == 3
    release.set()
    result = await task
    assert list(result) == [str(index) for index in range(8)]
    assert result == {str(index): {"value": index} for index in range(8)}
    assert peak == 3


async def test_widget_errors_and_duplicate_ids_keep_existing_contract(monkeypatch):
    """An unavailable widget cannot fail its siblings or reorder duplicate IDs."""

    async def resolve(**kwargs):
        widget = kwargs["widget"]
        if widget.get("fail"):
            raise PermissionError("access_denied")
        await asyncio.sleep(0)
        return widget["value"]

    monkeypatch.setattr(service, "resolve_widget_data", resolve)
    dashboard = SimpleNamespace(
        widgets=[
            {"id": "same", "value": 1},
            {"value": "skipped"},
            {"id": "denied", "fail": True},
            {"id": "same", "value": 2},
        ]
    )
    result = await service.resolve_dashboard_data(
        user_id="user", app_id="app", dashboard=dashboard
    )
    assert result == {"same": 2, "denied": {"error": "access_denied"}}


async def test_widget_permission_memos_are_independent_and_live(monkeypatch):
    """Concurrent widgets cannot inherit or share a previous allow decision."""
    caches = []

    async def resolve(**kwargs):
        cache = permissions_cache_get()
        assert cache == {}
        assert live_permissions_required()
        caches.append(cache)
        cache[("widget",)] = kwargs["widget"]["id"]
        await asyncio.sleep(0)
        assert cache[("widget",)] == kwargs["widget"]["id"]
        return {"ok": True}

    monkeypatch.setattr(service, "resolve_widget_data", resolve)
    dashboard = SimpleNamespace(widgets=[{"id": str(index)} for index in range(7)])
    with isolated_permissions_cache():
        parent = permissions_cache_get()
        parent[("existing",)] = "allow"
        await service.resolve_dashboard_data(
            user_id="user", app_id="app", dashboard=dashboard
        )
        assert permissions_cache_get() is parent
        assert parent == {("existing",): "allow"}
    assert len({id(cache) for cache in caches}) == 7


async def test_cancelled_dashboard_cancels_running_and_queued_reads(monkeypatch):
    """Disconnect cancellation leaves no running or queued query work."""
    started = asyncio.Event()
    active = finished = 0

    async def resolve(**kwargs):
        nonlocal active, finished
        active += 1
        if active == 3:
            started.set()
        try:
            await asyncio.Event().wait()
        finally:
            active -= 1
            finished += 1

    monkeypatch.setattr(service, "resolve_widget_data", resolve)
    task = asyncio.create_task(
        service.resolve_dashboard_data(
            user_id="user",
            app_id="app",
            dashboard=SimpleNamespace(widgets=[{"id": str(i)} for i in range(8)]),
        )
    )
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert active == 0
    assert finished == 3
