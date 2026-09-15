"""Tests for ChatThread.agent_id and HAS_AGENT_PREFERENCE edge."""

from datetime import datetime, timezone
from typing import Any, Dict
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from app.models.edges import HasAgentPreference
from app.models.nodes import ChatThread


@pytest.mark.asyncio
async def test_chat_thread_has_agent_id_field():
    """ChatThread must carry agent_id stamped at create."""
    thread = await ChatThread(
        user_id="u1",
        workspace_id="ws1",
        provider_id="jvagent",
        agent_id="iris",
        title="t",
    ).save()
    assert thread.agent_id == "iris"


def test_has_agent_preference_edge_fields():
    """Edge declares provider_id, agent_id, updated_at."""
    edge = HasAgentPreference(
        provider_id="jvagent",
        agent_id="iris",
        updated_at=datetime.now(timezone.utc).isoformat(),
    )
    assert edge.provider_id == "jvagent"
    assert edge.agent_id == "iris"
    assert edge.updated_at


# ---------------------------------------------------------------------------
# Task 5 — POST /chat/threads agent resolution chain
# ---------------------------------------------------------------------------


async def _create_workspace(client: AsyncClient) -> str:
    """Create an organization workspace and pin it as the caller's active scope."""
    resp = await client.post("/api/workspaces", json={"name": "Task5 WS"})
    assert resp.status_code == 200, resp.text
    workspace_id = resp.json()["workspace"]["id"]
    # Thread create resolves workspace via ``resolve_workspace_id_from_request``.
    # Pin scope server-side so preference edges and new threads share the same
    # workspace even if the header hint fails membership validation transiently.
    scope = await client.put(
        "/api/users/me/scope",
        json={"workspace_id": workspace_id},
    )
    assert scope.status_code == 200, scope.text
    assert scope.json().get("active_workspace_id") == workspace_id
    return workspace_id


def _scope_headers(workspace_id: str) -> dict[str, str]:
    return {"X-Integral-Scope": f"ws:{workspace_id}"}


@pytest.mark.asyncio
async def test_create_thread_uses_explicit_agent_id(
    authenticated_client: AsyncClient,
    test_user,
):
    """Body's agent_id wins over preference / catalog."""
    workspace_id = await _create_workspace(authenticated_client)
    response = await authenticated_client.post(
        "/api/chat/threads",
        headers=_scope_headers(workspace_id),
        json={"provider_id": "jvagent", "agent_id": "iris"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["agent_id"] == "iris"


@pytest.mark.asyncio
async def test_create_thread_falls_back_to_preference(
    authenticated_client: AsyncClient,
    test_user,
):
    """Body omits agent_id → use the workspace preference."""
    workspace_id = await _create_workspace(authenticated_client)
    pref = await authenticated_client.put(
        f"/api/workspaces/{workspace_id}/agent-preference",
        headers=_scope_headers(workspace_id),
        json={"provider_id": "jvagent", "agent_id": "aiva"},
    )
    assert pref.status_code == 200, pref.text
    response = await authenticated_client.post(
        "/api/chat/threads",
        headers=_scope_headers(workspace_id),
        json={"provider_id": "jvagent"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["agent_id"] == "aiva"


@pytest.mark.asyncio
async def test_create_thread_falls_back_to_catalog_default(
    authenticated_client: AsyncClient,
    test_user,
):
    """No body agent, no preference → catalog default (first)."""
    workspace_id = await _create_workspace(authenticated_client)
    fake_agents = [{"id": "iris", "name": "Iris", "description": ""}]
    with patch(
        "app.services.chat_providers.jvagent_provider.JvagentProvider.list_agents",
        new=AsyncMock(return_value=fake_agents),
    ):
        response = await authenticated_client.post(
            "/api/chat/threads",
            headers=_scope_headers(workspace_id),
            json={"provider_id": "jvagent"},
        )
    assert response.status_code == 200, response.text
    assert response.json()["agent_id"] == "iris"


@pytest.mark.asyncio
async def test_create_thread_422_when_no_agent_resolvable(
    authenticated_client: AsyncClient,
    test_user,
):
    """No body, no preference, empty catalog → 422."""
    workspace_id = await _create_workspace(authenticated_client)
    with patch(
        "app.services.chat_providers.jvagent_provider.JvagentProvider.list_agents",
        new=AsyncMock(return_value=[]),
    ):
        response = await authenticated_client.post(
            "/api/chat/threads",
            headers=_scope_headers(workspace_id),
            json={"provider_id": "jvagent"},
        )
    assert response.status_code == 422, response.text


# ---------------------------------------------------------------------------
# Task 6 — GET /chat/threads optional provider_id + agent_id filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_threads_filters_by_agent(
    authenticated_client: AsyncClient,
    test_user,
):
    workspace_id = await _create_workspace(authenticated_client)
    # create three threads — two under iris, one under aiva
    for agent in ["iris", "aiva", "iris"]:
        await authenticated_client.post(
            "/api/chat/threads",
            headers=_scope_headers(workspace_id),
            json={"provider_id": "jvagent", "agent_id": agent},
        )

    response = await authenticated_client.get(
        "/api/chat/threads",
        headers=_scope_headers(workspace_id),
        params={"provider_id": "jvagent", "agent_id": "iris"},
    )
    assert response.status_code == 200, response.text
    threads = response.json()["threads"]
    assert len(threads) == 2
    assert all(t["agent_id"] == "iris" for t in threads)


@pytest.mark.asyncio
async def test_list_threads_no_filter_returns_all(
    authenticated_client: AsyncClient,
    test_user,
):
    workspace_id = await _create_workspace(authenticated_client)
    for agent in ["iris", "aiva"]:
        await authenticated_client.post(
            "/api/chat/threads",
            headers=_scope_headers(workspace_id),
            json={"provider_id": "jvagent", "agent_id": agent},
        )
    response = await authenticated_client.get(
        "/api/chat/threads",
        headers=_scope_headers(workspace_id),
    )
    threads = response.json()["threads"]
    assert len(threads) == 2


# ---------------------------------------------------------------------------
# Task 7 — dispatcher passes thread.agent_id to provider via extra_data
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_passes_thread_agent_id_to_provider(
    authenticated_client: AsyncClient,
    test_user,
):
    """The dispatcher must hand thread.agent_id to the provider via extra_data."""
    workspace_id = await _create_workspace(authenticated_client)
    create = await authenticated_client.post(
        "/api/chat/threads",
        headers=_scope_headers(workspace_id),
        json={"provider_id": "jvagent", "agent_id": "aiva"},
    )
    thread_id = create.json()["id"]

    seen: Dict[str, Any] = {}

    async def fake_stream(self, ctx):
        seen["agent_id"] = (ctx.extra_data or {}).get("agent_id")
        if False:
            yield  # make this an async generator without yielding anything

    with patch(
        "app.services.chat_providers.jvagent_provider.JvagentProvider.stream_turn",
        new=fake_stream,
    ):
        await authenticated_client.post(
            f"/api/chat/threads/{thread_id}/messages",
            headers=_scope_headers(workspace_id),
            json={"text": "hi"},
        )

    assert seen.get("agent_id") == "aiva"
