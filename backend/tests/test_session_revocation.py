"""Password reset and admin deactivation end existing sessions.

Both flows previously replaced the hash / flipped ``is_active`` but left every
issued access + refresh token valid until natural expiry.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from jvspatial.api.auth.service import AuthenticationService


@pytest.fixture
def revoke_spy(monkeypatch) -> AsyncMock:
    """Spy on AuthenticationService.revoke_all_user_tokens."""
    spy = AsyncMock(return_value=0)
    monkeypatch.setattr(AuthenticationService, "revoke_all_user_tokens", spy)
    return spy


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_password_reset_revokes_all_user_tokens(test_user, revoke_spy):
    """consume_reset_token revokes every session of the AuthUser."""
    from app.services import password_reset

    auth_user_id = getattr(test_user, "user_id", None) or test_user.id
    token, token_hash = password_reset.generate_reset_token()
    password_reset._store_token_on_user(test_user, token_hash)
    await test_user.save()

    ok, err = await password_reset.consume_reset_token(token, "aBrandNewPassw0rd!")
    assert ok is True, err

    revoke_spy.assert_awaited_once_with(auth_user_id)


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_admin_deactivate_revokes_all_user_tokens(
    authenticated_admin_client: AsyncClient, test_user2, revoke_spy
):
    """Admin deactivate revokes every session of the target AuthUser."""
    r = await authenticated_admin_client.post(
        f"/api/admin/users/{test_user2.id}/deactivate"
    )
    assert r.status_code == 200, r.text
    assert r.json()["user"]["is_active"] is False

    auth_user_id = getattr(test_user2, "user_id", None) or test_user2.id
    revoke_spy.assert_awaited_once_with(auth_user_id)
