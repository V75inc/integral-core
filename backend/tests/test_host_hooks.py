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
        result = await hooks.record_usage_event(
            workspace_id="n.Workspace.test",
            run_id="run-123",
            idempotency_key="idem-abc",
            thread_id="thread-1",
            source="platform",
            input_tokens=10,
            output_tokens=20,
            model_id="openai/gpt-4o-mini",
        )
        assert result == {
            "ok": False,
            "error": "usage_record_failed",
            "workspace_id": "n.Workspace.test",
        }
        failure = hooks.get_last_usage_record_failure()
        assert failure is not None
        assert failure["workspace_id"] == "n.Workspace.test"
        assert "RuntimeError" in failure["error"]
        assert failure["run_id"] == "run-123"
        assert failure["idempotency_key"] == "idem-abc"
        assert failure["thread_id"] == "thread-1"
        assert failure["source"] == "platform"
        assert failure["input_tokens"] == 10
        assert failure["output_tokens"] == 20
        assert failure["model_id"] == "openai/gpt-4o-mini"
        assert "run_id" in failure["kwargs_keys"]
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


@pytest.mark.asyncio
async def test_subscription_enforcement_shim_installs_authorizer():
    from app.api.errors import InsufficientPermissionsError
    from app.services import host_hooks as hooks

    class _Req:
        def __init__(self, roles):
            self.state = type("S", (), {"user": type("U", (), {"roles": roles})()})()

    hooks.set_subscription_enforcement(True)
    try:
        with pytest.raises(InsufficientPermissionsError):
            await hooks.assert_entitlement_mutation_allowed(
                _Req([]), "grant", "n.Workspace.test"
            )
        await hooks.assert_entitlement_mutation_allowed(
            _Req(["admin"]), "grant", "n.Workspace.test"
        )
    finally:
        hooks.set_subscription_enforcement(False)


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


def test_mount_host_middleware_required_failure_aborts_and_isolates():
    from app.services import host_hooks as hooks
    from app.services.host_hooks import MiddlewareRegistration

    mounted: list = []

    class _App:
        def add_middleware(self, mw):
            mounted.append(mw)

    def boom():
        raise RuntimeError("mw boom")

    def ok():
        return "PolicyMW"

    with pytest.raises(RuntimeError, match="required host middleware"):
        hooks.mount_host_middleware(
            _App(),
            registrations=[
                MiddlewareRegistration(factory=boom, required=True),
                MiddlewareRegistration(factory=ok, required=True),
            ],
        )
    assert mounted == []


def test_mount_host_middleware_optional_failure_continues():
    from app.services import host_hooks as hooks
    from app.services.host_hooks import MiddlewareRegistration

    mounted: list = []

    class _App:
        def add_middleware(self, mw):
            mounted.append(mw)

    def boom():
        raise RuntimeError("optional boom")

    def ok():
        return "PolicyMW"

    result = hooks.mount_host_middleware(
        _App(),
        registrations=[
            MiddlewareRegistration(factory=boom, required=False),
            MiddlewareRegistration(factory=ok, required=True),
        ],
    )
    assert result == ["PolicyMW"]
    assert mounted == ["PolicyMW"]


def test_mount_host_middleware_none_skip_does_not_abort():
    from app.services import host_hooks as hooks
    from app.services.host_hooks import MiddlewareRegistration

    mounted: list = []

    class _App:
        def add_middleware(self, mw):
            mounted.append(mw)

    def skip():
        return None

    def ok():
        return "LaterMW"

    result = hooks.mount_host_middleware(
        _App(),
        registrations=[
            MiddlewareRegistration(factory=skip, required=True),
            MiddlewareRegistration(factory=ok, required=True),
        ],
    )
    assert result == ["LaterMW"]
    assert mounted == ["LaterMW"]
