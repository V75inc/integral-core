"""Qualification of a non-registered package against injected no-op ports."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agentive.services import profile_management
from app.agentive.services.web_chat_package import (
    SLUG,
    ManagedWebChatPackage,
    WebChatProvisionChange,
)
from app.api.errors import InsufficientPermissionsError, ResourceConflictError
from app.connectors.channel_packages import (
    ChannelInstallation,
    ChannelScope,
    ChannelServingStatus,
)

SCOPE = ChannelScope("owner", "workspace")
INSTALLATION = ChannelInstallation(
    SCOPE.workspace_id, SLUG, "provision", "epoch", False
)


@pytest.fixture
def ports(monkeypatch):
    manager = AsyncMock()
    publication = {"key": "public", "audience": "public", "tools": [], "version": 4}
    route = {"enabled": False, "generation": 8, "profile_key": "public"}
    settings = SimpleNamespace(
        revision=5,
        public_web_chat=route,
        profiles={"public": {"published": publication}},
    )
    monkeypatch.setattr(profile_management, "require_profile_manager", manager)
    monkeypatch.setattr(profile_management, "record", AsyncMock(return_value=settings))

    async def ready(scope, current):
        return ChannelServingStatus(
            scope.workspace_id,
            SLUG,
            current.provision_id if current else None,
            current.route_epoch if current else None,
            "accepting" if current and current.enabled else "ready",
        )

    store = SimpleNamespace(
        observe=AsyncMock(return_value=INSTALLATION), commit=AsyncMock()
    )
    host = SimpleNamespace(
        probe=AsyncMock(side_effect=ready), receive_verified=AsyncMock()
    )
    return SimpleNamespace(
        manager=manager,
        publication=publication,
        route=route,
        store=store,
        host=host,
        adapter=ManagedWebChatPackage(store, host),
    )


@pytest.mark.asyncio
async def test_install_does_not_adopt_a_legacy_assigned_or_enabled_route(ports):
    ports.store.observe.return_value = None
    ports.store.commit.return_value = INSTALLATION
    ports.route["enabled"] = True
    assert await ports.adapter.package.lifecycle.observe(SCOPE) is None
    assert await ports.adapter.package.lifecycle.install(SCOPE) == INSTALLATION
    ports.store.commit.assert_awaited_once_with(
        SCOPE, WebChatProvisionChange("install_disabled", None, 5)
    )
    assert ports.adapter.package.serving.full_page_link is None
    ports.host.receive_verified.assert_not_awaited()


@pytest.mark.asyncio
async def test_exact_profile_revision_and_provision_are_cas_preconditions(ports):
    await ports.adapter.assign_profile(SCOPE, INSTALLATION, 5, "public")
    ports.store.commit.assert_awaited_once_with(
        SCOPE, WebChatProvisionChange("assign", INSTALLATION, 5, "public")
    )
    with pytest.raises(ResourceConflictError):
        await ports.adapter.assign_profile(SCOPE, INSTALLATION, 4, "public")
    with pytest.raises(ResourceConflictError):
        await ports.adapter.package.lifecycle.set_enabled(
            SCOPE, replace(INSTALLATION, route_epoch="stale"), True
        )
    assert ports.store.commit.await_count == 1


@pytest.mark.asyncio
async def test_ready_and_tool_free_are_both_required_for_enablement(ports):
    assert (
        await ports.adapter.package.serving.probe(SCOPE, INSTALLATION)
    ).state == "ready"
    ports.publication["tools"] = ["must-not-run"]
    assert (
        await ports.adapter.package.serving.probe(SCOPE, INSTALLATION)
    ).state == "unavailable"
    with pytest.raises(ResourceConflictError):
        await ports.adapter.package.lifecycle.set_enabled(SCOPE, INSTALLATION, True)
    ports.publication["tools"] = []
    ports.host.probe.side_effect = None
    ports.host.probe.return_value = ChannelServingStatus(
        SCOPE.workspace_id, SLUG, "another-provision", "epoch", "ready"
    )
    with pytest.raises(ResourceConflictError):
        await ports.adapter.package.lifecycle.set_enabled(SCOPE, INSTALLATION, True)
    ports.store.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_manager_and_outage_recovery_do_not_depend_on_serving(ports):
    ports.manager.side_effect = InsufficientPermissionsError("Denied")
    with pytest.raises(InsufficientPermissionsError):
        await ports.adapter.package.lifecycle.remove(SCOPE, INSTALLATION)
    ports.store.observe.assert_not_awaited()
    ports.manager.side_effect = None
    ports.host.probe.side_effect = RuntimeError("No transport")
    await ports.adapter.package.lifecycle.set_enabled(SCOPE, INSTALLATION, False)
    await ports.adapter.package.lifecycle.remove(SCOPE, INSTALLATION)
    assert [call.args[1].action for call in ports.store.commit.await_args_list] == [
        "disable",
        "remove",
    ]
    ports.host.probe.assert_not_awaited()


@pytest.mark.asyncio
async def test_profile_descriptor_projects_only_current_public_tool_free_options(ports):
    ports.publication.update(
        name="Visitor guide", persona="Not for management projection"
    )
    setup = ports.adapter.package.profile_setup
    assert setup is not None
    descriptor = await setup.describe(SCOPE, INSTALLATION)
    assert descriptor.editable and descriptor.profile_revision == 5
    assert [(option.key, option.name) for option in descriptor.eligible_profiles] == [
        ("public", "Visitor guide")
    ]
    ports.publication["tools"] = ["not eligible"]
    descriptor = await setup.describe(SCOPE, INSTALLATION)
    assert descriptor.eligible_profiles == ()
    ports.store.commit.assert_not_awaited()
