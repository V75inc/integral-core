"""Admin gate for programmatic User node creation."""

import jwt as pyjwt
import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from tests.conftest import TEST_BASE_URL, USE_LIVE_SERVER, get_app


@pytest.mark.asyncio
async def test_create_user_requires_platform_admin(client, test_user):
    """Authenticated non-admin caller is rejected (403).

    The default ``authenticated_client`` often carries ``admin`` because
    jvspatial promotes the first registered AuthUser — mint an explicit
    ``roles=["user"]`` JWT instead.
    """
    user_id = getattr(test_user, "user_id", None) or getattr(test_user, "id", "")
    if not user_id:
        pytest.skip("no test_user available (live-server mode)")

    token = pyjwt.encode(
        {
            "user_id": user_id,
            "email": getattr(test_user, "email", "") or "test@example.com",
            "name": getattr(test_user, "display_name", "") or "Test User",
            "roles": ["user"],
            "permissions": [],
        },
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )

    if USE_LIVE_SERVER:
        non_admin = AsyncClient(
            base_url=TEST_BASE_URL,
            timeout=10.0,
            headers={"Authorization": f"Bearer {token}"},
        )
    else:
        non_admin = AsyncClient(
            transport=ASGITransport(app=get_app()),
            base_url="http://test",
            timeout=10.0,
            headers={"Authorization": f"Bearer {token}"},
        )
    try:
        response = await non_admin.post(
            "/api/users",
            json={
                "email": "orphan@example.com",
                "display_name": "Orphan User",
            },
        )
        assert response.status_code == 403, response.text
    finally:
        await non_admin.aclose()


@pytest.mark.asyncio
async def test_create_user_allowed_for_platform_admin(
    authenticated_admin_client: AsyncClient,
):
    response = await authenticated_admin_client.post(
        "/api/users",
        json={
            "email": "admin-created@example.com",
            "display_name": "Admin Created",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body.get("user", {}).get("display_name") == "Admin Created"
