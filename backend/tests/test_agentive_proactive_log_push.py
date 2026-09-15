"""D-06 regression: /proactive/push → 308 redirect + /proactive/log-push → 202."""

import pytest


@pytest.mark.asyncio
async def test_proactive_old_path_308_redirects_to_log_push(authenticated_client):
    r = await authenticated_client.post(
        "/api/agentive/proactive/push",
        json={"channel": "in_app", "message_type": "test", "payload": {}},
        follow_redirects=False,
    )
    assert r.status_code == 308
    assert r.headers["location"] == "/api/agentive/proactive/log-push"


@pytest.mark.asyncio
async def test_proactive_log_push_returns_202(authenticated_client):
    r = await authenticated_client.post(
        "/api/agentive/proactive/log-push",
        json={"channel": "in_app", "message_type": "test", "payload": {}},
    )
    assert r.status_code == 202
    body = r.json()
    assert body.get("accepted") is True
    assert "logged_at" in body
    assert "pushed" not in body, "D-06: response must not claim pushed: true"
