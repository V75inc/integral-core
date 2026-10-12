"""Trusted host access overrides fail closed and never create durable grants."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import host_hooks


@pytest.mark.asyncio
@pytest.mark.unit
async def test_legacy_definition_lookup_can_be_read_only():
    from app.services.application_definitions import get_active_application_definition

    definition = SimpleNamespace(id="definition", status="active", revision=2)
    app = SimpleNamespace(
        active_definition_id="",
        active_definition_revision=0,
        nodes=AsyncMock(return_value=[definition]),
        save=AsyncMock(),
    )
    assert (
        await get_active_application_definition(app, repair_legacy_pointer=False)
        is definition
    )
    assert app.active_definition_id == ""
    app.save.assert_not_awaited()
    # Existing lifecycle callers retain the migration behavior by default.
    assert await get_active_application_definition(app) is definition
    assert app.active_definition_id == "definition"
    app.save.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_installed_package_identity_survives_compiled_definition(monkeypatch):
    from app.services import application_definitions, entitlements

    app = SimpleNamespace(
        installed_from_library_id="library",
        source_operational_model_slug="sample",
        workspace_id="workspace",
    )
    monkeypatch.setattr(
        application_definitions,
        "get_active_application_definition",
        AsyncMock(
            return_value=SimpleNamespace(
                canonical_manifest={"package": {"class": "commercial_app"}}
            )
        ),
    )
    gate = AsyncMock()
    monkeypatch.setattr(entitlements, "require_active_entitlement", gate)
    await entitlements.require_installed_app_entitlement(app)
    gate.assert_awaited_once_with(
        workspace_id="workspace",
        package_meta={"class": "commercial_app", "slug": "sample"},
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_absent_truthy_and_failed_host_resolvers(monkeypatch):
    monkeypatch.setattr(host_hooks, "_entitlement_access_resolver", None)
    assert not await host_hooks.host_allows_entitlement_access("ws", "key", "slug")
    monkeypatch.setattr(
        host_hooks, "_entitlement_access_resolver", AsyncMock(return_value="yes")
    )
    with pytest.raises(TypeError):
        await host_hooks.host_allows_entitlement_access("ws", "key", "slug")
    monkeypatch.setattr(
        host_hooks,
        "_entitlement_access_resolver",
        AsyncMock(side_effect=RuntimeError("offline")),
    )
    with pytest.raises(RuntimeError):
        await host_hooks.host_allows_entitlement_access("ws", "key", "slug")


@pytest.mark.asyncio
@pytest.mark.unit
async def test_validated_owner_key_controls_readiness(monkeypatch):
    from app.services import model_credential_resolver as resolver

    monkeypatch.setattr(resolver.settings, "INTEGRAL_AGENT_KEY_MODE", "hybrid")
    owner = AsyncMock(return_value=SimpleNamespace(validated_at="2026-10-12"))
    monkeypatch.setattr(resolver, "_workspace_owner_credential", owner)
    monkeypatch.setattr(resolver, "_credential_funds_byok", lambda record: True)
    assert await resolver.resolve_agent_key_access("ws") == "byok"
    monkeypatch.setattr(resolver, "_credential_funds_byok", lambda record: False)
    assert await resolver.resolve_agent_key_access("ws") == "platform"
    monkeypatch.setattr(resolver.settings, "INTEGRAL_AGENT_KEY_MODE", "byo_strict")
    assert await resolver.resolve_agent_key_access("ws") == "unavailable"
    monkeypatch.setattr(resolver.settings, "INTEGRAL_AGENT_KEY_MODE", "platform_only")
    assert await resolver.resolve_agent_key_access("ws") == "platform"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_unvalidated_key_does_not_claim_ready(monkeypatch):
    from app.services import model_credential_resolver as resolver

    monkeypatch.setattr(resolver.settings, "INTEGRAL_AGENT_KEY_MODE", "hybrid")
    monkeypatch.setattr(
        resolver,
        "_workspace_owner_credential",
        AsyncMock(return_value=SimpleNamespace(validated_at=None)),
    )
    monkeypatch.setattr(resolver, "_credential_funds_byok", lambda record: True)
    assert await resolver.resolve_agent_key_access("ws") == "platform"
