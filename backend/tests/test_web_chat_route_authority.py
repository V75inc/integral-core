"""Test detached authority with synthetic read-only ports."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agentive.connectors.external_chat import ExternalChatProvision
from app.agentive.services import profile_management
from app.api.errors import InsufficientPermissionsError
from app.connectors.channel_packages import ChannelInstallation, ChannelScope
from app.web.web_chat_route_authority import (
    ApprovedWebChatReference,
    WebChatRouteAuthority,
)

pytestmark = pytest.mark.smoke


@pytest.fixture
def route_authority(monkeypatch):
    """Provide separate durable policy and trusted reference test data."""
    provision = ExternalChatProvision("w", "public_web_chat", "p")
    approved = ApprovedWebChatReference(
        "host-reference", "https://example.invalid", provision, "4", "public", 1
    )
    route = {
        "provision_id": "p",
        "generation": 4,
        "enabled": False,
        "profile_key": "public",
    }
    profile = {"key": "public", "version": 1, "audience": "public", "tools": []}
    record = SimpleNamespace(
        public_web_chat=route, profiles={"public": {"published": profile}}
    )
    get_record = AsyncMock(
        side_effect=lambda workspace: record if workspace == "w" else None
    )
    manager = AsyncMock()
    monkeypatch.setattr(profile_management, "record", get_record)
    monkeypatch.setattr(profile_management, "require_profile_manager", manager)
    host = SimpleNamespace(
        by_key=AsyncMock(
            side_effect=lambda key: approved if key == approved.key else None
        ),
        by_provision=AsyncMock(return_value=approved),
        transport_ready=AsyncMock(return_value=True),
        install_ready=AsyncMock(return_value=False),
    )
    scope = ChannelScope("manager", "w")
    installation = ChannelInstallation("w", "public_web_chat", "p", "4", False)
    return (
        WebChatRouteAuthority(host),
        host,
        scope,
        installation,
        record,
        profile,
        manager,
    )


@pytest.mark.asyncio
async def test_disabled_can_be_ready_without_visitor_access(route_authority):
    """A proven disabled provision is ready for enable, not public visitors."""
    authority, _, scope, installation, _, _, manager = route_authority
    assert (await authority.probe(scope, installation)).state == "ready"
    manager.assert_awaited_once_with("manager", "w")
    assert await authority.resolve("host-reference") is None
    assert (await authority.probe(scope, None)).state == "unavailable"
    manager.side_effect = InsufficientPermissionsError("No manager")
    with pytest.raises(InsufficientPermissionsError):
        await authority.probe(scope, installation)


@pytest.mark.asyncio
async def test_install_readiness_needs_explicit_host_and_proven_absence(
    route_authority,
):
    """Neither an existing installation nor absent infrastructure permits Add."""
    authority, host, scope, installation, record, _, _ = route_authority
    host.install_ready.return_value = True
    assert (await authority.probe(scope, None)).state == "unavailable"
    host.install_ready.assert_not_awaited()
    record.public_web_chat.clear()
    host.install_ready.return_value = False
    assert (await authority.probe(scope, None)).state == "unavailable"
    host.install_ready.return_value = True
    assert (await authority.probe(scope, None)).state == "ready"
    assert await authority.resolve("host-reference") is None

    async def installed(workspace):
        assert workspace == "w"
        record.public_web_chat["provision_id"] = installation.provision_id
        return True

    host.install_ready.side_effect = installed
    assert (await authority.probe(scope, None)).state == "unavailable"


@pytest.mark.asyncio
async def test_exact_active_reference_is_pinned_to_publication(route_authority):
    """Republication cannot move an issued request onto a new publication."""
    authority, host, scope, installation, record, profile, _ = route_authority
    record.public_web_chat["enabled"] = True
    installation = replace(installation, enabled=True)
    assert (await authority.probe(scope, installation)).state == "accepting"
    resolved = await authority.resolve("host-reference")
    assert resolved.publication == ("public", 1)
    assert await authority.readiness(resolved)
    assert await authority.resolve("other-reference") is None
    profile["version"] = 2
    assert await authority.resolve("host-reference") is None
    assert (await authority.probe(scope, installation)).state == "unavailable"
    current = replace(
        host.by_provision.return_value, key="new-reference", published_version=2
    )
    host.by_key.side_effect = lambda key: current if key == current.key else None
    host.by_provision.return_value = current
    assert (await authority.resolve("new-reference")).publication == ("public", 2)
    assert not await authority.readiness(resolved)


@pytest.mark.parametrize(
    "condition",
    [
        "disable",
        "epoch",
        "assignment",
        "wrong-index",
        "bad-origin",
        "outage",
        "removed",
        "tools",
    ],
)
@pytest.mark.asyncio
async def test_visitor_route_loses_authority_on_current_state_change(
    route_authority, condition
):
    """Resolve again after readiness, and reject stale or malformed host state."""
    authority, host, _, _, record, profile, _ = route_authority
    record.public_web_chat["enabled"] = True
    assert await authority.resolve("host-reference") is not None

    async def change(_approved):
        if condition == "disable":
            record.public_web_chat["enabled"] = False
        elif condition == "epoch":
            record.public_web_chat["generation"] = 5
        elif condition == "assignment":
            record.public_web_chat["profile_key"] = None
        elif condition == "wrong-index":
            host.by_provision.return_value = None
        elif condition == "bad-origin":
            unapproved = replace(
                host.by_provision.return_value, origin="http://example.invalid"
            )
            host.by_key.side_effect = None
            host.by_key.return_value = unapproved
            host.by_provision.return_value = unapproved
        elif condition == "outage":
            return False
        elif condition == "removed":
            record.public_web_chat["provision_id"] = None
        elif condition == "tools":
            profile["tools"] = ["private"]
        return True

    host.transport_ready.side_effect = change
    assert await authority.resolve("host-reference") is None
