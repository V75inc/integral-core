"""Regression guard for the fast-auth test fixture path."""

import pytest


@pytest.mark.asyncio
async def test_fast_auth_client_can_read_auth_me(authenticated_client):
    """Fast-path bootstrap must produce a JWT accepted by TestAuthBypassMiddleware."""
    resp = await authenticated_client.get("/api/auth/me")
    assert resp.status_code == 200
    data = resp.json()
    user = data.get("user") or data
    assert user.get("email") == "test@example.com"


@pytest.mark.asyncio
async def test_fast_auth_test_user_resolves_graph_node(authenticated_client, test_user):
    """Fast-path bootstrap must wire AuthUser to a graph User node."""
    assert test_user is not None
    assert getattr(test_user, "user_id", None) or getattr(test_user, "id", None)
