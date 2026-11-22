"""Management-only composition persists setup without a visitor host."""

from unittest.mock import AsyncMock

import pytest

from app.agentive.api.managed_channels import install_managed_channel_registry
from app.agentive.services import profile_management, public_web_chat
from app.agentive.services.web_chat_package import SLUG
from app.api.errors import ResourceConflictError
from app.connectors.channel_packages import ChannelPackageRegistry, ChannelScope
from app.models.edges import IS_MEMBER_OF
from app.models.nodes import Workspace
from app.web.web_chat_management_assembly import register_web_chat_management

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]
ROOT = "/api/agentive/managed-channels"
PROFILE = "public"


def _row(response):
    assert response.status_code == 200, response.text
    [item] = response.json()["channels"]
    assert item["definition"]["slug"] == SLUG
    assert "full_page_url" not in item and "embed_url" not in item
    return item


async def test_setup_persists_but_cannot_enable_without_serving_composition(
    authenticated_client, test_user, second_user, second_user_client, monkeypatch
):
    from app.main import app

    assert str(authenticated_client.base_url) == "http://test"
    response = await authenticated_client.post(
        "/api/workspaces", json={"name": "Disposable management-only workspace"}
    )
    assert response.status_code in (200, 201), response.text
    workspace_id = response.json()["workspace"]["id"]
    workspace = await Workspace.find_one({"id": workspace_id})
    await second_user.connect(workspace, edge=IS_MEMBER_OF, role="member")
    scope = ChannelScope(test_user.id, workspace_id)
    query = {"workspace_id": workspace_id}
    headers = {"X-Integral-Scope": f"ws:{workspace_id}"}
    state_key = "integral_managed_channel_registry"
    monkeypatch.setattr(app.state, state_key, None, raising=False)
    delattr(app.state, state_key)
    before = await authenticated_client.get(ROOT, params=query, headers=headers)
    assert before.status_code == 200 and before.json() == {"channels": []}

    async def publish_fixture(profiles, _bindings, _native, _route):
        profiles[PROFILE] = {
            "published": {
                **profile_management.starter(PROFILE),
                "name": "Synthetic public profile",
                "tools": [],
                "version": 3,
            }
        }

    await profile_management.mutate_config(
        test_user.id, workspace_id, 0, publish_fixture
    )
    # A wholly separate legacy host hook cannot enable this package.
    ambient_ready = AsyncMock(return_value=True)
    monkeypatch.setattr(public_web_chat, "transport_ready", ambient_ready)

    registry = ChannelPackageRegistry()
    register_web_chat_management(registry)
    routes_before = tuple(app.routes)
    paths = tuple(getattr(route, "path", "") for route in routes_before)
    assert not any(
        path.startswith(
            ("/api/public/web-chat", "/api/v1/web-chat", "/chat/", "/embed/")
        )
        or (path.startswith(ROOT) and path.endswith(("/share", "/embed-share")))
        for path in paths
    )
    install_managed_channel_registry(app, registry)
    assert tuple(app.routes) == routes_before

    registered = _row(
        await authenticated_client.get(ROOT, params=query, headers=headers)
    )
    assert registered["state"] == "registered" and registered["can_install"] is True
    installed = _row(
        await authenticated_client.post(
            f"{ROOT}/{SLUG}/install", params=query, headers=headers
        )
    )
    assert installed["state"] == "inactive" and installed["can_enable"] is False
    target = {key: installed[key] for key in ("provision_id", "route_epoch")}
    config_path = f"{ROOT}/{SLUG}/configuration"
    config = await authenticated_client.get(
        config_path, params={**query, **target}, headers=headers
    )
    assert config.status_code == 200 and config.json()["editable"] is True
    saved = _row(
        await authenticated_client.put(
            config_path,
            params=query,
            headers=headers,
            json={
                **target,
                "expected_profile_revision": config.json()["profile_revision"],
                "profile_key": PROFILE,
            },
        )
    )
    assert saved["state"] == "inactive" and saved["can_enable"] is False
    expected = {key: saved[key] for key in ("provision_id", "route_epoch")}

    # A restart/second adapter sees the same real persisted provision.
    reloaded = ChannelPackageRegistry()
    register_web_chat_management(reloaded)
    package = reloaded.resolve(SLUG)
    assert package is not None and package.profile_setup is not None
    persisted = await package.lifecycle.observe(scope)
    assert persisted.provision_id == expected["provision_id"]
    assert persisted.route_epoch == expected["route_epoch"]
    assert persisted.enabled is False
    profile = await package.profile_setup.describe(scope, persisted)
    assert profile.profile_key == PROFILE and profile.published_version == 3
    with pytest.raises(ResourceConflictError, match="not ready"):
        await package.lifecycle.set_enabled(scope, persisted, True)

    rejected = await authenticated_client.put(
        f"{ROOT}/{SLUG}/enabled",
        params=query,
        headers=headers,
        json={**expected, "enabled": True},
    )
    assert rejected.status_code == 409
    assert await package.lifecycle.observe(scope) == persisted
    assert await package.profile_setup.describe(scope, persisted) == profile
    assert (
        await second_user_client.get(ROOT, params=query, headers=headers)
    ).status_code == 403
    assert tuple(app.routes) == routes_before
    ambient_ready.assert_not_awaited()
