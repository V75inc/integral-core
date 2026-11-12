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


@pytest.mark.asyncio
async def test_delivery_uses_authenticated_workspace_not_payload(
    authenticated_client, monkeypatch
):
    """Payload scope cannot choose the destination workspace."""
    from unittest.mock import AsyncMock

    from app.services import chat_proactive_bridge, request_scope

    resolve = AsyncMock(return_value="authorized-workspace")
    deliver = AsyncMock(return_value=None)
    monkeypatch.setattr(request_scope, "resolve_workspace_id_from_request", resolve)
    monkeypatch.setattr(
        chat_proactive_bridge, "persist_proactive_message_on_thread", deliver
    )
    response = await authenticated_client.post(
        "/api/agentive/proactive/log-push",
        json={
            "channel": "in_app",
            "message_type": "test",
            "payload": {
                "content": "test",
                "session_id": "session-1",
                "delivery_id": "delivery-1",
                "workspace_id": "other",
            },
        },
    )
    assert response.status_code == 202
    assert response.json()["delivered"] is False
    assert deliver.await_args.kwargs["workspace_id"] == "authorized-workspace"
    assert deliver.await_args.kwargs["session_id"] == "session-1"


@pytest.mark.asyncio
async def test_delivery_without_stable_id_is_refused(authenticated_client, monkeypatch):
    """The old endpoint never sends an unrepeatable message."""
    from unittest.mock import AsyncMock

    from app.services import chat_proactive_bridge

    deliver = AsyncMock()
    monkeypatch.setattr(
        chat_proactive_bridge, "persist_proactive_message_on_thread", deliver
    )
    response = await authenticated_client.post(
        "/api/agentive/proactive/log-push",
        json={"payload": {"content": "hello", "session_id": "session"}},
    )
    assert response.status_code == 202
    assert response.json()["delivered"] is False
    assert response.json()["delivery_reason"] == "delivery_id_required"
    deliver.assert_not_awaited()
