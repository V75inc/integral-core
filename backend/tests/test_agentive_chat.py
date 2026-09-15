"""Agentive chat API and system uplink (JWT + service key)."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.agentive.connectors import ChatTurnContext
from app.agentive.connectors.jvagent_connector import JvAgentConnector
from app.agentive.services.uplink_registry import AgentConnection, uplink_registry
from app.main import app

# Part of the per-PR smoke gate (see pyproject [tool.pytest.ini_options] markers).
# These are auth-isolation tests: they assert that a service key is required,
# that the right one is accepted, and that neither answer leaks into the next
# test. That belongs on every PR — the two files were both outside the marker
# when a leaked `INTEGRAL_SERVICE_KEY` in one of them turned the other red on
# `main` for two days while every PR stayed green.
pytestmark = pytest.mark.smoke


@pytest.mark.asyncio
async def test_chat_message_with_mock_connector(authenticated_client, monkeypatch):
    from app.agentive.connectors.base import ChatTurnResult

    fake_conn = AgentConnection(
        config_id="cfg_sys_test",
        agent_type="jvagent",
        uplink_url="",
        capabilities=[],
        scope="system",
        preferences={},
    )

    async def mock_get_system():
        return fake_conn

    class _FakeConnector:
        async def send_turn(self, ctx, *, preferences):
            return ChatTurnResult(
                message="ok-from-agent",
                session_id="s_fix",
                agent_user_id="test@example.com",
            )

    def mock_get_connector(_agent_type: str):
        return _FakeConnector()

    monkeypatch.setattr(uplink_registry, "get_system_agent", mock_get_system)
    monkeypatch.setattr(
        "app.agentive.api.chat.get_chat_connector",
        mock_get_connector,
    )

    r = await authenticated_client.post(
        "/api/agentive/chat/message",
        json={"message": "hello"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ok") is True
    assert body.get("message") == "ok-from-agent"
    assert body.get("session_id") == "s_fix"


@pytest.mark.asyncio
async def test_chat_message_requires_authentication():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post(
            "/api/agentive/chat/message",
            json={"message": "hello"},
        )
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_chat_message_503_when_no_system_agent(authenticated_client):
    r = await authenticated_client.post(
        "/api/agentive/chat/message",
        json={"message": "hello"},
    )
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_register_system_requires_service_key():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post(
            "/api/agentive/uplink/register-system",
            json={"jvagent_agent_id": "ag_test"},
        )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_register_system_and_heartbeat_with_service_key():
    sk = "integral-test-service-key-for-agentive-only__________"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        reg = await ac.post(
            "/api/agentive/uplink/register-system",
            headers={"X-Integral-Service-Key": sk},
            json={
                "jvagent_agent_id": "ag_integration_test",
                "jvagent_base_url": "http://127.0.0.1:9",
                "capabilities": [],
            },
        )
    assert reg.status_code == 200, reg.text
    data = reg.json()
    assert data.get("status") == "registered"
    cid = data.get("agent_config_id")
    assert cid

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        hb = await ac.get(
            "/api/agentive/uplink/heartbeat-system",
            headers={"X-Integral-Service-Key": sk},
        )
    assert hb.status_code == 200
    assert hb.json().get("status") == "alive"

    uplink_registry._agents.pop(cid, None)


@pytest.mark.asyncio
async def test_jvagent_connector_maps_response(monkeypatch):
    import httpx

    async def fake_post(self, url, **kwargs):
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={
                "user_id": "u@example.com",
                "session_id": "sess_1",
                "response": "Hi there",
            },
            request=req,
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    monkeypatch.setattr(
        "app.agentive.connectors.jvagent_connector.settings.JVAGENT_BASE_URL",
        "http://localhost:1",
    )
    monkeypatch.setattr(
        "app.agentive.connectors.jvagent_connector.settings.INTEGRAL_JVAGENT_AGENT_ID",
        "ag_x",
    )

    conn = JvAgentConnector()
    ctx = ChatTurnContext(email="u@example.com", message="hello")
    out = await conn.send_turn(ctx, preferences={})
    assert out.error is None
    assert out.message == "Hi there"
    assert out.session_id == "sess_1"
