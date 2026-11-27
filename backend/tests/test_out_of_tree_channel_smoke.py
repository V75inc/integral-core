"""Cross-slice check through Integral's real authenticated ASGI app."""

import pytest

from app.agentive.api.managed_channels import (
    _STATE_KEY,
    install_managed_channel_registry,
)
from app.connectors.channel_package_discovery import load_approved_channel_package
from app.connectors.channel_packages import ChannelPackageRegistry
from tests.test_channel_package_discovery import installed_wheel  # noqa: F401
from tests.test_channel_package_discovery import APPROVAL


@pytest.mark.asyncio
async def test_external_wheel_through_real_connector_routes(
    authenticated_client, installed_wheel  # noqa: F811
):
    """A package outside Core passes the real authenticated management API."""
    from app.main import app

    registry = ChannelPackageRegistry()
    load_approved_channel_package(registry, APPROVAL)
    assert registry.resolve(APPROVAL.slug) is not None

    ws_response = await authenticated_client.get("/api/workspaces")
    ws_response.raise_for_status()
    data = ws_response.json()
    rows = (
        data if isinstance(data, list) else data.get("workspaces") or data.get("items")
    )
    assert rows
    workspace = rows[0]["id"]
    url = "/api/agentive/managed-channels"
    headers = {"X-Integral-Scope": f"ws:{workspace}"}
    params = {"workspace_id": workspace}

    async def request(method, suffix="", *, expected=200, **kwargs):
        response = await authenticated_client.request(
            method, url + suffix, headers=headers, params=params, **kwargs
        )
        assert response.status_code == expected, response.text
        if expected == 200:
            (entry,) = response.json()["channels"]
            assert entry["definition"]["slug"] == APPROVAL.slug
            assert "full_page_url" not in entry
            return entry

    previous = getattr(app.state, _STATE_KEY, None)
    if previous is not None:
        delattr(app.state, _STATE_KEY)
    install_managed_channel_registry(app, registry)
    try:
        entry = await request("GET")
        assert (
            entry["state"],
            entry["action"],
            entry["connected"],
            entry["can_install"],
        ) == ("registered", "add", False, True)
        slug = f"/{APPROVAL.slug}"
        installed = await request("POST", slug + "/install")
        assert (installed["state"], installed["action"], installed["connected"]) == (
            "inactive",
            "configure",
            True,
        )
        version = {k: installed[k] for k in ("provision_id", "route_epoch")}
        active = await request(
            "PUT", slug + "/enabled", json={**version, "enabled": True}
        )
        assert (active["state"], active["connected"]) == ("active", True)
        await request(
            "PUT", slug + "/enabled", json={**version, "enabled": False}, expected=409
        )
        active_version = {k: active[k] for k in ("provision_id", "route_epoch")}
        disabled = await request(
            "PUT", slug + "/enabled", json={**active_version, "enabled": False}
        )
        assert (disabled["state"], disabled["connected"]) == ("inactive", True)
        assert disabled["route_epoch"] != active["route_epoch"]
        params.update({k: disabled[k] for k in ("provision_id", "route_epoch")})
        removed = await request("DELETE", slug)
        assert (removed["state"], removed["action"], removed["connected"]) == (
            "registered",
            "add",
            False,
        )
    finally:
        if hasattr(app.state, _STATE_KEY):
            delattr(app.state, _STATE_KEY)
        if previous is not None:
            install_managed_channel_registry(app, previous)
