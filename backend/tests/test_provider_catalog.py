"""Tests for provider-side agent catalog discovery."""

from unittest.mock import patch

import pytest
from httpx import AsyncClient

from app.services.chat_providers.jvagent_provider import JvagentProvider


@pytest.mark.asyncio
async def test_jvagent_list_agents_embed_path():
    """Embed-mode list_agents() proxies jvagent.embed.list_agents()."""
    provider = JvagentProvider()
    fake_agents = [
        {
            "id": "agt-1",
            "namespace": "ns",
            "name": "Iris",
            "alias": "iris",
            "enabled": True,
            "description": "Friendly assistant",
        },
        {
            "id": "agt-2",
            "namespace": "ns",
            "name": "Aiva",
            "alias": "aiva",
            "enabled": True,
            "description": "Sales associate",
        },
    ]
    with (
        patch.object(provider, "_embed_configured", return_value=True),
        patch("jvagent.embed.list_agents", return_value=fake_agents),
    ):
        result = await provider.list_agents()

    assert len(result) == 2
    # id = jvspatial Agent node id (used directly by embed.interact_stream).
    assert result[0]["id"] == "agt-1"
    # name = alias (friendly display label).
    assert result[0]["name"] == "iris"
    assert result[0]["description"] == "Friendly assistant"
    assert result[1]["id"] == "agt-2"
    assert result[1]["name"] == "aiva"


@pytest.mark.asyncio
async def test_jvagent_list_agents_disabled_returns_empty():
    """When neither transport is wired, catalog is empty."""
    provider = JvagentProvider()
    with (
        patch.object(provider, "_embed_configured", return_value=False),
        patch.object(provider, "_http_configured", return_value=False),
    ):
        result = await provider.list_agents()
    assert result == []


@pytest.mark.asyncio
async def test_jvagent_list_agents_skips_disabled_agents():
    """enabled=False agents are omitted from the catalog."""
    provider = JvagentProvider()
    fake_agents = [
        {"id": "1", "name": "On", "alias": "on", "enabled": True, "description": ""},
        {"id": "2", "name": "Off", "alias": "off", "enabled": False, "description": ""},
    ]
    with (
        patch.object(provider, "_embed_configured", return_value=True),
        patch("jvagent.embed.list_agents", return_value=fake_agents),
    ):
        result = await provider.list_agents()
    # id = jvspatial node id; disabled entry omitted entirely.
    assert [a["id"] for a in result] == ["1"]
    assert [a["name"] for a in result] == ["on"]


@pytest.mark.asyncio
async def test_jvagent_list_agents_returns_empty_on_runtime_exception():
    """If jvagent.embed.list_agents raises, catalog falls back to empty."""
    provider = JvagentProvider()
    with (
        patch.object(provider, "_embed_configured", return_value=True),
        patch("jvagent.embed.list_agents", side_effect=RuntimeError("boom")),
    ):
        result = await provider.list_agents()
    assert result == []


@pytest.mark.asyncio
async def test_get_provider_agents_endpoint(authenticated_client: AsyncClient):
    """Endpoint returns the provider's agent catalog."""
    fake_agents = [
        {"id": "iris", "name": "Iris", "description": "Friendly assistant"},
    ]
    with patch(
        "app.services.chat_providers.jvagent_provider.JvagentProvider.list_agents",
        return_value=fake_agents,
    ):
        response = await authenticated_client.get("/api/chat/providers/jvagent/agents")
    assert response.status_code == 200
    data = response.json()
    assert data["agents"][0]["id"] == "iris"


@pytest.mark.asyncio
async def test_get_provider_agents_unknown_provider_400(
    authenticated_client: AsyncClient,
):
    response = await authenticated_client.get(
        "/api/chat/providers/no-such-provider/agents"
    )
    assert response.status_code == 400
