"""AGT-03: /agentive/chat/message dispatch tests."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_chat_dispatch_happy_path_returns_typed_response(
    authenticated_client, monkeypatch
):
    """Happy-path dispatch through stub connector returns ChatTurnResponse shape (AGT-03)."""
    from app.agentive.connectors.base import ChatTurnResult
    from app.agentive.services import uplink_registry as uplink_registry_mod

    class _FakeConfig:
        agent_type = "mcp"
        preferences: dict = {}

    async def _mock_get_system():
        return _FakeConfig()

    class _FakeConnector:
        async def send_turn(self, ctx, *, preferences):
            return ChatTurnResult(
                message="dispatched",
                session_id="sess-d",
                agent_user_id=ctx.email,
            )

    monkeypatch.setattr(
        uplink_registry_mod.uplink_registry, "get_system_agent", _mock_get_system
    )
    monkeypatch.setattr(
        "app.agentive.api.chat.get_chat_connector",
        lambda _t: _FakeConnector(),
    )

    r = await authenticated_client.post(
        "/api/agentive/chat/message",
        json={"message": "hi"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) >= {
        "ok",
        "message",
        "session_id",
        "agent_user_id",
        "agent_type",
    }
    assert body["ok"] is True
    assert body["agent_type"] == "mcp"
    assert body["message"] == "dispatched"
    assert body["session_id"] == "sess-d"


@pytest.mark.asyncio
async def test_chat_dispatch_503_when_no_system_agent(
    authenticated_client, monkeypatch
):
    """503 when uplink_registry has no system agent."""
    from app.agentive.services import uplink_registry as uplink_registry_mod

    async def _mock_no_agent():
        return None

    monkeypatch.setattr(
        uplink_registry_mod.uplink_registry, "get_system_agent", _mock_no_agent
    )

    r = await authenticated_client.post(
        "/api/agentive/chat/message",
        json={"message": "hi"},
    )
    assert r.status_code == 503, r.text
