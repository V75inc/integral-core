"""GET/PUT /workspaces/{id}/agent-preference."""

import pytest
from httpx import AsyncClient


async def _create_workspace(client: AsyncClient, name: str = "AP Test WS") -> str:
    """Create an org workspace and return its id."""
    resp = await client.post("/api/workspaces", json={"name": name})
    assert resp.status_code == 200, resp.text
    return resp.json()["workspace"]["id"]


@pytest.mark.asyncio
async def test_get_preference_unset_returns_null(
    authenticated_client: AsyncClient,
    test_user,
):
    workspace_id = await _create_workspace(authenticated_client)
    response = await authenticated_client.get(
        f"/api/workspaces/{workspace_id}/agent-preference"
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"preference": None}


@pytest.mark.asyncio
async def test_put_preference_creates_and_reads_back(
    authenticated_client: AsyncClient,
    test_user,
):
    workspace_id = await _create_workspace(authenticated_client)
    put = await authenticated_client.put(
        f"/api/workspaces/{workspace_id}/agent-preference",
        json={"provider_id": "jvagent", "agent_id": "iris"},
    )
    assert put.status_code == 200, put.text
    assert put.json() == {"preference": {"provider_id": "jvagent", "agent_id": "iris"}}

    get = await authenticated_client.get(
        f"/api/workspaces/{workspace_id}/agent-preference"
    )
    assert get.status_code == 200, get.text
    assert get.json()["preference"]["agent_id"] == "iris"


@pytest.mark.asyncio
async def test_put_preference_is_idempotent(
    authenticated_client: AsyncClient,
    test_user,
):
    """Two PUTs leave exactly one edge — latest wins."""
    workspace_id = await _create_workspace(authenticated_client)
    await authenticated_client.put(
        f"/api/workspaces/{workspace_id}/agent-preference",
        json={"provider_id": "jvagent", "agent_id": "iris"},
    )
    await authenticated_client.put(
        f"/api/workspaces/{workspace_id}/agent-preference",
        json={"provider_id": "jvagent", "agent_id": "aiva"},
    )
    get = await authenticated_client.get(
        f"/api/workspaces/{workspace_id}/agent-preference"
    )
    assert get.status_code == 200, get.text
    assert get.json()["preference"]["agent_id"] == "aiva"


@pytest.mark.asyncio
async def test_put_preference_unknown_provider_400(
    authenticated_client: AsyncClient,
    test_user,
):
    workspace_id = await _create_workspace(authenticated_client)
    response = await authenticated_client.put(
        f"/api/workspaces/{workspace_id}/agent-preference",
        json={"provider_id": "no-such-provider", "agent_id": "x"},
    )
    assert response.status_code == 400, response.text
