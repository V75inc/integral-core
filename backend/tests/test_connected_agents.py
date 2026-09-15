"""M3b-2 — user-facing connected-agents list + revoke.

Two bearer-authed endpoints let a user see and revoke the external OAuth
clients (MCP agents) that hold an active refresh-token grant for them:

  * ``GET    /api/users/me/connected-agents``             — list distinct clients
  * ``DELETE /api/users/me/connected-agents/{client_id}`` — revoke a client's grant

Both queries are keyed on ``context.user_id == <resolved principal>`` so a
caller can NEVER see or revoke another user's grants. The DELETE additionally
filters by ``client_id`` so revoking client X for user A leaves user B's grant
for the SAME client X untouched — the cross-user isolation property pinned
below.

The endpoints are plain ``@endpoint`` handlers (NOT the MCP mount), so a
bearer-bearing ``AsyncClient`` over ``ASGITransport`` suffices (no
``LifespanManager`` needed). Grants are seeded by persisting ``OAuthClient`` +
``OAuthRefreshToken`` Objects directly — the same way jvspatial's own
refresh-store tests do — so no full OAuth round-trip is required.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient


def _client() -> AsyncClient:
    """Return an ASGITransport ``AsyncClient`` (closed by the caller)."""
    from tests.conftest import get_app

    return AsyncClient(
        transport=ASGITransport(app=get_app()),
        base_url="http://test",
        timeout=15.0,
    )


async def _bootstrap_user_token() -> tuple[str, str]:
    """Bootstrap an auth user + JWT. Returns ``(token, auth_user_id)``.

    ``auth_user_id`` is the JWT ``sub`` == ``resolve_principal_id`` for the
    bearer — the value the endpoints key their token queries on.
    """
    from tests.conftest import _bootstrap_test_user_fast

    email = f"connected-agents-{uuid.uuid4().hex}@example.com"
    token, user_node = await _bootstrap_test_user_fast(
        email=email, password="testpassword123", name="Connected Agents"
    )
    return token, user_node.user_id


async def _seed_client(
    *, client_id: str | None = None, client_name: str = "Test MCP Agent"
):
    """Persist an ``OAuthClient`` Object directly. Returns the record."""
    from jvspatial.api.auth.oauth.models import OAuthClient

    return await OAuthClient.create(
        client_id=client_id or f"cli_{uuid.uuid4().hex}",
        client_secret_hash=None,
        client_name=client_name,
        redirect_uris=["https://agent.example/cb"],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        scope="integral",
        token_endpoint_auth_method="none",
    )


async def _seed_grant(
    *,
    user_id: str,
    client_id: str,
    scope: str = "integral",
    is_active: bool = True,
    expires_in: timedelta = timedelta(days=7),
    family_id: str | None = None,
    created_at: datetime | None = None,
):
    """Persist an ``OAuthRefreshToken`` Object directly. Returns the record."""
    from jvspatial.api.auth.oauth.models import OAuthRefreshToken

    return await OAuthRefreshToken.create(
        token_hash=f"hash_{uuid.uuid4().hex}",
        user_id=user_id,
        client_id=client_id,
        scope=scope,
        resource="https://api.example/mcp",
        expires_at=datetime.now(timezone.utc) + expires_in,
        is_active=is_active,
        family_id=family_id or f"fam_{uuid.uuid4().hex}",
        created_at=created_at or datetime.now(timezone.utc),
    )


async def _active_grants(*, user_id: str, client_id: str) -> int:
    """Count this user's active refresh tokens for a client (test oracle)."""
    from jvspatial.api.auth.oauth.models import OAuthRefreshToken

    rows = await OAuthRefreshToken.find(
        {
            "context.user_id": user_id,
            "context.client_id": client_id,
            "context.is_active": True,
        }
    )
    return len(rows)


@pytest.mark.asyncio
async def test_list_returns_only_callers_clients(
    bind_fresh_graph_context_for_async_tests,
):
    """User A sees A's clients; B's clients for B never leak into A's list."""
    token_a, uid_a = await _bootstrap_user_token()
    _token_b, uid_b = await _bootstrap_user_token()

    client_a = await _seed_client(client_name="Agent A")
    client_b = await _seed_client(client_name="Agent B")

    await _seed_grant(user_id=uid_a, client_id=client_a.client_id)
    await _seed_grant(user_id=uid_b, client_id=client_b.client_id)

    async with _client() as ac:
        resp = await ac.get(
            "/api/users/me/connected-agents",
            headers={"Authorization": f"Bearer {token_a}"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    agents = body if isinstance(body, list) else body.get("agents", body)
    ids = {a["client_id"] for a in agents}
    assert client_a.client_id in ids, agents
    assert client_b.client_id not in ids, agents
    # Surfaced metadata for A's client.
    row_a = next(a for a in agents if a["client_id"] == client_a.client_id)
    assert row_a["client_name"] == "Agent A", row_a
    assert "integral" in row_a["scopes"], row_a
    assert row_a["granted_at"], row_a


@pytest.mark.asyncio
async def test_list_excludes_expired_and_inactive(
    bind_fresh_graph_context_for_async_tests,
):
    """Expired-by-time and is_active=False grants are filtered from the list."""
    token, uid = await _bootstrap_user_token()

    active_client = await _seed_client(client_name="Active")
    expired_client = await _seed_client(client_name="Expired")
    inactive_client = await _seed_client(client_name="Revoked")

    await _seed_grant(user_id=uid, client_id=active_client.client_id)
    await _seed_grant(
        user_id=uid,
        client_id=expired_client.client_id,
        expires_in=timedelta(seconds=-5),  # already expired
    )
    await _seed_grant(user_id=uid, client_id=inactive_client.client_id, is_active=False)

    async with _client() as ac:
        resp = await ac.get(
            "/api/users/me/connected-agents",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    agents = body if isinstance(body, list) else body.get("agents", body)
    ids = {a["client_id"] for a in agents}
    assert active_client.client_id in ids, agents
    assert expired_client.client_id not in ids, agents
    assert inactive_client.client_id not in ids, agents


@pytest.mark.asyncio
async def test_list_dedupes_multiple_tokens_per_client(
    bind_fresh_graph_context_for_async_tests,
):
    """Several active tokens for one client collapse into a single row.

    ``granted_at`` is the most-recent ``created_at`` across the client's
    active tokens (the latest rotation in the grant family).
    """
    token, uid = await _bootstrap_user_token()
    client = await _seed_client(client_name="Multi Token")

    older = datetime.now(timezone.utc) - timedelta(days=2)
    newer = datetime.now(timezone.utc) - timedelta(hours=1)
    await _seed_grant(user_id=uid, client_id=client.client_id, created_at=older)
    await _seed_grant(user_id=uid, client_id=client.client_id, created_at=newer)

    async with _client() as ac:
        resp = await ac.get(
            "/api/users/me/connected-agents",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    agents = body if isinstance(body, list) else body.get("agents", body)
    matching = [a for a in agents if a["client_id"] == client.client_id]
    assert len(matching) == 1, matching
    # granted_at reflects the most-recent token's created_at.
    granted = matching[0]["granted_at"]
    assert granted.startswith(newer.isoformat()[:16]), (granted, newer.isoformat())


@pytest.mark.asyncio
async def test_revoke_deactivates_all_callers_tokens_for_client(
    bind_fresh_graph_context_for_async_tests,
):
    """DELETE deactivates EVERY active token A holds for the client, returns count.

    A subsequent list for A no longer shows the revoked client.
    """
    token, uid = await _bootstrap_user_token()
    client = await _seed_client(client_name="To Revoke")

    # Two active tokens for the same client (two rotations of one grant).
    await _seed_grant(user_id=uid, client_id=client.client_id)
    await _seed_grant(user_id=uid, client_id=client.client_id)
    assert await _active_grants(user_id=uid, client_id=client.client_id) == 2

    async with _client() as ac:
        resp = await ac.delete(
            f"/api/users/me/connected-agents/{client.client_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["revoked"] == 2, resp.text

        # Subsequent list no longer shows the client.
        listed = await ac.get(
            "/api/users/me/connected-agents",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert listed.status_code == 200, listed.text
    body = listed.json()
    agents = body if isinstance(body, list) else body.get("agents", body)
    assert client.client_id not in {a["client_id"] for a in agents}, agents
    assert await _active_grants(user_id=uid, client_id=client.client_id) == 0


@pytest.mark.asyncio
async def test_revoke_does_not_touch_other_users_grant_for_same_client(
    bind_fresh_graph_context_for_async_tests,
):
    """Cross-user isolation: A revoking shared client X leaves B's X grant active.

    The DELETE filters by ``user_id AND client_id`` — revoking a client the two
    users SHARE must deactivate only the caller's tokens.
    """
    token_a, uid_a = await _bootstrap_user_token()
    _token_b, uid_b = await _bootstrap_user_token()

    # ONE client both users have authorized.
    shared = await _seed_client(client_name="Shared Agent")
    await _seed_grant(user_id=uid_a, client_id=shared.client_id)
    await _seed_grant(user_id=uid_b, client_id=shared.client_id)
    assert await _active_grants(user_id=uid_a, client_id=shared.client_id) == 1
    assert await _active_grants(user_id=uid_b, client_id=shared.client_id) == 1

    async with _client() as ac:
        resp = await ac.delete(
            f"/api/users/me/connected-agents/{shared.client_id}",
            headers={"Authorization": f"Bearer {token_a}"},
        )

    assert resp.status_code == 200, resp.text
    assert resp.json()["revoked"] == 1, resp.text
    # A's grant gone; B's UNTOUCHED.
    assert await _active_grants(user_id=uid_a, client_id=shared.client_id) == 0
    assert await _active_grants(user_id=uid_b, client_id=shared.client_id) == 1


@pytest.mark.asyncio
async def test_revoke_unknown_client_is_404_and_touches_nothing(
    bind_fresh_graph_context_for_async_tests,
):
    """Revoking a client the caller has no active grant for is a 4xx (404).

    Crucially, it must NOT touch any other user's tokens for that client.
    """
    token_a, _uid_a = await _bootstrap_user_token()
    _token_b, uid_b = await _bootstrap_user_token()

    # B has an active grant for the client; A has none.
    client = await _seed_client(client_name="B-only Agent")
    await _seed_grant(user_id=uid_b, client_id=client.client_id)

    async with _client() as ac:
        resp = await ac.delete(
            f"/api/users/me/connected-agents/{client.client_id}",
            headers={"Authorization": f"Bearer {token_a}"},
        )

    assert 400 <= resp.status_code < 500, resp.text
    assert resp.status_code == 404, resp.text
    # B's grant is untouched by A's failed revoke.
    assert await _active_grants(user_id=uid_b, client_id=client.client_id) == 1


@pytest.mark.asyncio
async def test_list_no_bearer_is_401(bind_fresh_graph_context_for_async_tests):
    """No Authorization header on the list endpoint -> 401 (auth-gated)."""
    async with _client() as ac:
        resp = await ac.get("/api/users/me/connected-agents")
    assert resp.status_code in (401, 403), resp.text


@pytest.mark.asyncio
async def test_revoke_no_bearer_is_401(bind_fresh_graph_context_for_async_tests):
    """No Authorization header on the revoke endpoint -> 401 (auth-gated)."""
    async with _client() as ac:
        resp = await ac.delete("/api/users/me/connected-agents/cli_anything")
    assert resp.status_code in (401, 403), resp.text
