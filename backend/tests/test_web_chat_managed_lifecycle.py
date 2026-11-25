"""Confirm the real managed Web Chat package through detached manager HTTP."""

from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agentive.api.managed_channels import install_managed_channel_registry
from app.agentive.services import profile_management
from app.agentive.services.web_chat_package import SLUG, ManagedWebChatPackage
from app.agentive.services.web_chat_provision_store import (
    ProfileRouteWebChatProvisionStore,
)
from app.connectors.channel_packages import ChannelPackageRegistry, ChannelServingStatus
from app.models.edges import IS_MEMBER_OF
from app.models.nodes import Workspace

PATH = "/api/agentive/managed-channels"
CONNECTOR = f"{PATH}/{SLUG}"
SYNTHETIC_PERSONA = "Unprojected fixture persona"
MATCH_FIELDS = ("provision_id", "route_epoch")


@pytest.fixture
async def managed_web_chat(authenticated_client, second_user, monkeypatch):
    """Build a detached Core request fixture with no real host or storage."""
    # ASGITransport in the test suite does not run the app lifespan or listen.
    assert str(authenticated_client.base_url) == "http://test"
    result = await authenticated_client.post(
        "/api/workspaces", json={"name": "Detached Web Chat qualification"}
    )
    assert result.status_code in {200, 201}, result.text
    workspace_id = result.json()["workspace"]["id"]
    workspace = await Workspace.find_one({"id": workspace_id})
    await second_user.connect(workspace, edge=IS_MEMBER_OF, role="member")

    def publication(audience, tools=(), *, key=None):
        return {
            "published": {
                **profile_management.starter(audience),
                "key": key or audience,
                "name": "Visitor guide",
                "persona": SYNTHETIC_PERSONA,
                "version": 3,
                "tools": list(tools),
            }
        }

    # Inject only storage and readback ports: real manager auth, profile policy,
    # provision store, package, revisions, and API endpoints run in this test.
    state = SimpleNamespace(
        id="fixture",
        revision=4,
        public_web_chat={},
        profiles={
            "public": publication("public"),
            "staff": publication("staff"),
            "p_"
            + "a"
            * 32: publication("public", ("fixture-private-tool",), key="p_" + "a" * 32),
        },
        bindings={},
        native_chat_profiles=[],
        ready=False,
    )

    async def record(selected, *, create=False):
        assert selected == workspace_id
        return deepcopy(state)

    @asynccontextmanager
    async def fixture_transaction():
        yield

    def compare_and_set(entity, query, mutation):
        assert entity == "node"
        if query != {"id": state.id, "context.revision": state.revision}:
            return None
        for key, value in mutation["$set"].items():
            setattr(state, key.removeprefix("context."), deepcopy(value))
        return {"id": state.id}

    writes = AsyncMock(side_effect=compare_and_set)
    monkeypatch.setattr(
        profile_management,
        "live_public_service_identity",
        AsyncMock(return_value=object()),
    )
    monkeypatch.setattr(profile_management, "record", record)
    monkeypatch.setattr(
        profile_management, "postgres_graph_transaction", fixture_transaction
    )
    monkeypatch.setattr(
        profile_management,
        "get_default_context",
        lambda: SimpleNamespace(database=SimpleNamespace(find_one_and_update=writes)),
    )
    monkeypatch.setattr(profile_management, "describe", AsyncMock(return_value={}))

    async def probe(scope, current):
        assert scope.workspace_id == workspace_id
        status = "accepting" if current and current.enabled else "ready"
        return ChannelServingStatus(
            workspace_id,
            SLUG,
            current.provision_id if current else None,
            current.route_epoch if current else None,
            status if state.ready else "unavailable",
        )

    async def unexpected_ingress(_message):
        pytest.fail("Management must not invoke visitor ingress")

    host = SimpleNamespace(probe=probe, receive_verified=unexpected_ingress)
    registry = ChannelPackageRegistry()
    registry.register(
        ManagedWebChatPackage(ProfileRouteWebChatProvisionStore(host), host).package
    )
    from app.main import app

    state_key = "integral_managed_channel_registry"
    monkeypatch.setattr(app.state, state_key, None, raising=False)
    delattr(app.state, state_key)
    install_managed_channel_registry(app, registry)
    return (
        {"X-Integral-Scope": f"ws:{workspace_id}"},
        {"workspace_id": workspace_id},
        state,
        writes,
    )


def _only_channel(response):
    assert response.status_code == 200, response.text
    [row] = response.json()["channels"]
    assert row["definition"]["slug"] == SLUG
    assert "full_page_url" not in row
    return row


def _match(row):
    return {key: row[key] for key in MATCH_FIELDS}


@pytest.mark.asyncio
async def test_real_managed_web_chat_configure_enable_disable(
    managed_web_chat, authenticated_client, second_user_client
):
    """A manager needs current role, profile revision, and host evidence."""
    headers, params, state, writes = managed_web_chat
    owner = authenticated_client
    options = {"headers": headers, "params": params}
    configuration = f"{CONNECTOR}/configuration"
    enabled_path = f"{CONNECTOR}/enabled"
    add = await owner.get(PATH, **options)
    row = _only_channel(add)
    assert row["action"] == "add" and row["can_install"] and not row["connected"]
    assert not state.ready
    assert (
        await second_user_client.post(f"{CONNECTOR}/install", **options)
    ).status_code == 403
    writes.assert_not_awaited()
    installed = _only_channel(await owner.post(f"{CONNECTOR}/install", **options))
    assert installed["action"] == "configure" and installed["connected"]
    assert installed["state"] == "inactive"
    assert not installed["can_enable"]  # No profile assigned yet.
    assert state.public_web_chat["enabled"] is False
    assert installed["configuration_kind"] == "published-public-profile-v1"
    assert installed["route_epoch"] == "1"

    assert (
        await owner.put(
            enabled_path, **options, json={**_match(installed), "enabled": True}
        )
    ).status_code == 409

    read_params = {**params, **_match(installed)}
    described = await owner.get(configuration, headers=headers, params=read_params)
    assert described.status_code == 200, described.text
    assert described.json() == {
        **_match(installed),
        "kind": "published-public-profile-v1",
        "profile_revision": 5,
        "profile_key": None,
        "theme": "system",
        "eligible_profiles": [{"key": "public", "name": "Visitor guide"}],
        "editable": True,
    }
    assert SYNTHETIC_PERSONA not in described.text
    command = {
        **_match(installed),
        "expected_profile_revision": 5,
        "profile_key": "public",
    }
    assert (
        await second_user_client.put(configuration, **options, json=command)
    ).status_code == 403
    assert (
        await owner.put(
            configuration,
            headers=headers,
            params={"workspace_id": "another"},
            json=command,
        )
    ).status_code == 400
    assert (
        await owner.put(
            configuration, **options, json={**command, "expected_profile_revision": 4}
        )
    ).status_code == 409
    assert writes.await_count == 1

    assigned = _only_channel(await owner.put(configuration, **options, json=command))
    assert assigned["state"] == "inactive" and not assigned["can_enable"]
    assert not state.public_web_chat["enabled"]
    assert _match(assigned) == {**_match(installed), "route_epoch": "2"}
    assert state.revision == 6 and state.public_web_chat["profile_key"] == "public"

    assert (
        await owner.put(
            enabled_path, **options, json={**_match(installed), "enabled": True}
        )
    ).status_code == 409
    assert (
        await owner.put(
            enabled_path, **options, json={**_match(assigned), "enabled": True}
        )
    ).status_code == 409
    assert writes.await_count == 2
    state.ready = True
    enabled = _only_channel(
        await owner.put(
            enabled_path, **options, json={**_match(assigned), "enabled": True}
        )
    )
    assert enabled["state"] == "active" and enabled["connected"]
    assert _match(enabled) == {**_match(assigned), "route_epoch": "3"}
    assert state.revision == 7 and state.public_web_chat["enabled"] is True
    read = await owner.get(
        configuration, headers=headers, params={**params, **_match(enabled)}
    )
    assert read.status_code == 200, read.text
    assert not read.json()["editable"]
    assert (
        read.json()["published_version"]
        == state.profiles["public"]["published"]["version"]
    )
    assert "full_page_url" not in read.json()
    assert "full_page_share_check_available" not in read.json()
    assert "approved_embed_policies" not in read.json()
    assert read.headers["cache-control"] == "no-store"

    state.ready = False
    disabled = _only_channel(
        await owner.put(
            enabled_path, **options, json={**_match(enabled), "enabled": False}
        )
    )
    assert disabled["action"] == "configure" and not disabled["can_enable"]
    assert _match(disabled) == {**_match(enabled), "route_epoch": "4"}
    assert state.revision == 8 and state.public_web_chat == {
        "provision_id": disabled["provision_id"],
        "profile_key": "public",
        "enabled": False,
        "generation": 4,
    }
    assert writes.await_count == 4
