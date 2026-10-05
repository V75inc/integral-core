"""OSS Core host_hooks stay no-ops until a host module registers."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_assert_platform_quota_noop_without_registration():
    from app.services import host_hooks as hooks

    hooks.register_platform_quota_assert(None)
    await hooks.assert_platform_quota("n.Workspace.test")


@pytest.mark.asyncio
async def test_record_usage_event_noop_without_registration():
    from app.services import host_hooks as hooks

    hooks.register_usage_event_recorder(None)
    hooks.clear_usage_record_failures()
    assert await hooks.record_usage_event(workspace_id="n.Workspace.test") is None
    assert hooks.get_last_usage_record_failure() is None


@pytest.mark.asyncio
async def test_record_usage_event_failure_is_observable():
    from app.services import host_hooks as hooks

    async def boom(**_kwargs):
        raise RuntimeError("ledger unavailable")

    hooks.clear_usage_record_failures()
    hooks.register_usage_event_recorder(boom)
    try:
        result = await hooks.record_usage_event(workspace_id="n.Workspace.test")
        assert result == {
            "ok": False,
            "error": "usage_record_failed",
            "workspace_id": "n.Workspace.test",
        }
        failure = hooks.get_last_usage_record_failure()
        assert failure is not None
        assert failure["workspace_id"] == "n.Workspace.test"
        assert "RuntimeError" in failure["error"]
        assert hooks.get_usage_record_failure_count() >= 1
    finally:
        hooks.register_usage_event_recorder(None)
        hooks.clear_usage_record_failures()


@pytest.mark.asyncio
async def test_enrich_workspace_export_passthrough():
    from app.services import host_hooks as hooks

    hooks.register_workspace_export_enricher(None)
    data = {"id": "n.Workspace.test", "name": "Demo"}
    out = await hooks.enrich_workspace_export(data, "n.Workspace.test")
    assert out is data
    assert "plan_key" not in out


def test_subscription_enforcement_defaults_off():
    from app.services import host_hooks as hooks

    hooks.set_subscription_enforcement(False)
    assert hooks.subscription_enforcement_enabled() is False


def test_middleware_factory_dedupes_same_callable():
    from app.services import host_hooks as hooks

    def factory():
        return None

    before = len(hooks.list_middleware_factories())
    hooks.register_middleware_factory(factory)
    hooks.register_middleware_factory(factory)
    after = hooks.list_middleware_factories()
    assert after.count(factory) == 1
    assert len(after) == before + 1
