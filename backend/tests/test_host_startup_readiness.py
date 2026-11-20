"""Required trusted host startup readiness checks."""

from unittest.mock import AsyncMock

import pytest

from app.services import host_hooks


@pytest.mark.asyncio
async def test_optional_registration_has_no_core_only_effect(monkeypatch):
    monkeypatch.setattr(host_hooks, "_startup_readiness_checks", [])
    await host_hooks.assert_startup_ready()


@pytest.mark.asyncio
async def test_registered_check_is_awaited_exactly_once(monkeypatch):
    monkeypatch.setattr(host_hooks, "_startup_readiness_checks", [])
    check = AsyncMock(return_value=None)
    host_hooks.register_startup_readiness_check(check)
    host_hooks.register_startup_readiness_check(check)
    await host_hooks.assert_startup_ready()
    check.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_check_blocks_startup(monkeypatch):
    monkeypatch.setattr(host_hooks, "_startup_readiness_checks", [])
    stale = AsyncMock(side_effect=RuntimeError("migration not yet validated"))
    later = AsyncMock(return_value=None)
    host_hooks.register_startup_readiness_check(stale)
    host_hooks.register_startup_readiness_check(later)
    with pytest.raises(RuntimeError, match="migration not yet validated"):
        await host_hooks.assert_startup_ready()
    later.assert_not_awaited()
