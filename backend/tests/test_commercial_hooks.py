"""Back-compat: commercial_hooks re-exports host_hooks (see test_host_hooks)."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_assert_ai_quota_noop_without_registration():
    from app.services import commercial_hooks as hooks

    hooks.register_ai_quota_assert(None)
    await hooks.assert_ai_quota("n.Workspace.test")


@pytest.mark.asyncio
async def test_record_ai_usage_noop_without_registration():
    from app.services import commercial_hooks as hooks

    hooks.register_ai_usage_recorder(None)
    assert await hooks.record_ai_usage(workspace_id="n.Workspace.test") is None


@pytest.mark.asyncio
async def test_enrich_workspace_export_passthrough():
    from app.services import commercial_hooks as hooks

    hooks.register_workspace_export_enricher(None)
    data = {"id": "n.Workspace.test", "name": "Demo"}
    out = await hooks.enrich_workspace_export(data, "n.Workspace.test")
    assert out is data


def test_subscription_enforcement_defaults_off():
    from app.services import commercial_hooks as hooks

    hooks.set_subscription_enforcement(False)
    assert hooks.subscription_enforcement_enabled() is False


def test_middleware_factory_dedupes_same_callable():
    from app.services import commercial_hooks as hooks

    def factory():
        return None

    before = len(hooks.list_middleware_factories())
    hooks.register_middleware_factory(factory)
    hooks.register_middleware_factory(factory)
    after = hooks.list_middleware_factories()
    assert after.count(factory) == 1
    assert len(after) == before + 1
