"""Managed channel registration and workspace projection without a served host."""

from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from app.connectors.channel_packages import (
    ChannelDefinition,
    ChannelInstallation,
    ChannelLifecycle,
    ChannelPackage,
    ChannelPackageRegistry,
    ChannelScope,
    ChannelServing,
    ChannelServingStatus,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
SCOPE = ChannelScope("synthetic-owner", "workspace-a")
INSTALLATION = ChannelInstallation(
    "workspace-a", "test_chat", "provision", "epoch", True
)


def _fixture(*, installation=None, state="ready", url=None):
    definition = ChannelDefinition(
        "test_chat",
        "Test",
        "Test chat",
        "An isolated channel fixture",
        "managed-text-v1",
    )
    evidence = ChannelServingStatus(
        SCOPE.workspace_id,
        definition.slug,
        installation.provision_id if installation else None,
        installation.route_epoch if installation else None,
        state,
    )
    return ChannelPackage(
        definition,
        ChannelLifecycle(
            observe=AsyncMock(return_value=installation),
            install=AsyncMock(),
            set_enabled=AsyncMock(),
            remove=AsyncMock(),
        ),
        ChannelServing(
            probe=AsyncMock(return_value=evidence),
            receive=AsyncMock(),
            full_page_link=AsyncMock(return_value=url),
        ),
    )


async def test_metadata_partial_and_duplicate_package_never_change_registration():
    """Register complete trusted code and its declaration as one operation."""
    registry = ChannelPackageRegistry()
    package = _fixture()
    for invalid in (
        package.definition,
        replace(package, lifecycle=None),
        replace(package, serving=replace(package.serving, receive=True)),
        replace(package, lifecycle=replace(package.lifecycle, install=lambda _: None)),
    ):
        with pytest.raises(TypeError):
            registry.register(invalid)
        assert registry.resolve(package.definition.slug) is None
        assert await registry.discover(SCOPE) == []
    with pytest.raises(ValueError):
        registry.register(
            replace(package, definition=replace(package.definition, version=2))
        )
    registry.register(package)
    conflicting = replace(package, serving=replace(package.serving, probe=AsyncMock()))
    with pytest.raises(ValueError, match="already registered"):
        registry.register(conflicting)
    assert registry.resolve(package.definition.slug) is package
    assert registry.resolve("planned_connector") is None
    (entry,) = await registry.discover(SCOPE)
    assert (entry.state, entry.action, entry.connected) == ("registered", "add", False)
    package.serving.receive.assert_not_awaited()
    package.lifecycle.install.assert_not_awaited()


@pytest.mark.parametrize(
    "installed,state,expected",
    [
        (None, "unavailable", None),
        (None, "accepting", None),
        (replace(INSTALLATION, enabled=False), "ready", ("inactive", True)),
        (replace(INSTALLATION, enabled=False), "unavailable", ("unavailable", False)),
        (replace(INSTALLATION, enabled=False), "accepting", ("unavailable", False)),
        (INSTALLATION, "ready", ("unavailable", False)),
        (INSTALLATION, "unavailable", ("unavailable", False)),
        (INSTALLATION, "accepting", ("active", False)),
    ],
)
async def test_projects_add_configure_installation_and_operational_state(
    installed, state, expected
):
    """Never infer Connected or Active from metadata or enablement alone."""
    package = _fixture(
        installation=installed, state=state, url="https://public.example/chat/route"
    )
    registry = ChannelPackageRegistry()
    registry.register(package)
    result = await registry.discover(SCOPE, include_sharing=True)
    if expected is None:
        assert result == []
    else:
        (entry,) = result
        assert (entry.state, entry.can_enable) == expected
        assert entry.connected and entry.action == "configure"
        assert entry.full_page_url == (
            "https://public.example/chat/route" if entry.state == "active" else None
        )
    assert package.serving.full_page_link.await_count == (
        state == "accepting" and installed == INSTALLATION
    )


async def test_never_projects_another_workspace_or_stale_route():
    """Scope and epoch are part of the current transport evidence."""
    registry = ChannelPackageRegistry()
    package = _fixture(installation=INSTALLATION, state="accepting")
    registry.register(package)
    assert await registry.discover(replace(SCOPE, workspace_id="outside")) == []
    assert await registry.discover(replace(SCOPE, actor_id="")) == []
    evidence = package.serving.probe.return_value
    for mismatched in (
        replace(evidence, workspace_id="outside"),
        replace(evidence, slug="other"),
        replace(evidence, provision_id="other"),
        replace(evidence, route_epoch="older"),
    ):
        package.serving.probe.return_value = mismatched
        (entry,) = await registry.discover(SCOPE)
        assert entry.state == "unavailable" and entry.full_page_url is None
    package.serving.full_page_link.assert_not_awaited()
    package.serving.probe.return_value = evidence
    package.lifecycle.observe.side_effect = [
        INSTALLATION,
        INSTALLATION,
        replace(INSTALLATION, route_epoch="new"),
    ]
    assert await registry.discover(SCOPE, include_sharing=True) == []


@pytest.mark.parametrize(
    "value",
    [
        "http://public.example/chat",
        "//public.example/chat",
        "javascript:alert(1)",
        "https://secret@public.example/chat",
        "https://public.example/chat?token=x",
        "https://public.example/chat#token",
        "https://public.example:bad/chat",
        "https://public.example/with space",
    ],
)
async def test_bad_public_link_is_never_projected(value):
    """An active route does not automatically get a valid URL."""
    registry = ChannelPackageRegistry()
    registry.register(_fixture(installation=INSTALLATION, state="accepting", url=value))
    (entry,) = await registry.discover(SCOPE, include_sharing=True)
    assert entry.state == "active" and entry.full_page_url is None


async def test_adapter_failure_does_not_offer_install_or_hide_known_installed_outage():
    """Keep only safely recoverable installed rows when readiness fails."""
    registry = ChannelPackageRegistry()
    package = _fixture(installation=INSTALLATION, state="accepting")
    registry.register(package)
    package.serving.probe.side_effect = RuntimeError("private diagnostic")
    (entry,) = await registry.discover(SCOPE)
    assert entry.state == "unavailable" and entry.full_page_url is None
    package.lifecycle.observe.side_effect = RuntimeError("private diagnostic")
    assert await registry.discover(SCOPE) == []


async def test_sharing_is_opt_in_and_does_not_define_active_state():
    """Default discovery does not fetch potentially publishable links."""
    registry = ChannelPackageRegistry()
    package = _fixture(
        installation=INSTALLATION,
        state="accepting",
        url="https://public.example/channel",
    )
    registry.register(package)
    (entry,) = await registry.discover(SCOPE)
    assert entry.state == "active" and entry.full_page_url is None
    package.serving.full_page_link.assert_not_awaited()
    package.serving.full_page_link.side_effect = RuntimeError("sharing offline")
    (entry,) = await registry.discover(SCOPE, include_sharing=True)
    assert entry.state == "active" and entry.full_page_url is None
