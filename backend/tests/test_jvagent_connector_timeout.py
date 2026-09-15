"""The HTTP connector must outlive a deep-thinking turn.

``POST /interact`` is a single blocking request spanning the whole agent turn.
A client timeout shorter than the turn does not degrade gracefully: it raises
``httpx.RequestError`` and the user is told "Could not reach the agent
service" — a transport failure reported for a healthy, still-running turn.
"""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.agentive.connectors.base import ChatTurnContext
from app.agentive.connectors.jvagent_connector import JvAgentConnector
from app.config import settings


def _ctx() -> ChatTurnContext:
    return ChatTurnContext(
        message="hello",
        email="user@example.com",
        session_id="s-1",
        focused_track_id=None,
        focused_space_id=None,
        extra={"workspace_id": "ws-1"},
    )


@pytest.mark.asyncio
async def test_turn_timeout_allows_minutes_not_seconds(monkeypatch):
    """The configured turn budget reaches the httpx client as the read timeout."""
    monkeypatch.setattr(settings, "JVAGENT_BASE_URL", "http://agent.test")
    monkeypatch.setattr(settings, "INTEGRAL_JVAGENT_AGENT_ID", "agent-1")
    monkeypatch.setattr(settings, "INTEGRAL_AGENT_TURN_TIMEOUT_SECONDS", 900.0)

    captured = {}
    real_client = httpx.AsyncClient

    def _spy(*args, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        client = real_client(*args, **kwargs)
        client.post = AsyncMock(  # type: ignore[method-assign]
            return_value=httpx.Response(
                200,
                json={"response": "ok", "session_id": "s-1"},
                request=httpx.Request("POST", "http://agent.test"),
            )
        )
        return client

    with patch("app.agentive.connectors.jvagent_connector.httpx.AsyncClient", _spy):
        result = await JvAgentConnector().send_turn(_ctx(), preferences={})

    assert result.error is None
    timeout = captured["timeout"]
    # Read is what a long turn spends its time on; connect stays short so a
    # genuinely unreachable service still fails fast.
    assert timeout.read == 900.0
    assert timeout.connect == 10.0


@pytest.mark.asyncio
async def test_read_budget_comfortably_exceeds_a_measured_deep_turn():
    """Guards the regression directly: measured resident-harness turns driving a
    reasoning model through plan → schema → query → write reach ~8 minutes. A
    ceiling at or below that returns a misleading transport error."""
    assert settings.INTEGRAL_AGENT_TURN_TIMEOUT_SECONDS >= 600.0
