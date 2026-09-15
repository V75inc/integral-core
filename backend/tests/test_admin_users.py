"""Platform admin user management API tests."""

import jwt as pyjwt
import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from tests.conftest import TEST_BASE_URL, USE_LIVE_SERVER, get_app


async def _non_admin_client(test_user) -> AsyncClient:
    user_id = getattr(test_user, "user_id", None) or getattr(test_user, "id", "")
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
        return AsyncClient(
            base_url=TEST_BASE_URL,
            timeout=10.0,
            headers={"Authorization": f"Bearer {token}"},
        )
    return AsyncClient(
        transport=ASGITransport(app=get_app()),
        base_url="http://test",
        timeout=10.0,
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.mark.asyncio
async def test_admin_overview_requires_platform_admin(client, test_user):
    non_admin = await _non_admin_client(test_user)
    try:
        resp = await non_admin.get("/api/admin/overview")
        assert resp.status_code == 403, resp.text
    finally:
        await non_admin.aclose()


@pytest.mark.asyncio
async def test_admin_overview_ok(authenticated_admin_client: AsyncClient):
    resp = await authenticated_admin_client.get("/api/admin/overview")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "total_users" in body
    assert "total_apps" in body


@pytest.mark.asyncio
async def test_admin_list_users_requires_platform_admin(client, test_user):
    non_admin = await _non_admin_client(test_user)
    try:
        resp = await non_admin.get("/api/admin/users")
        assert resp.status_code == 403, resp.text
    finally:
        await non_admin.aclose()


@pytest.mark.asyncio
async def test_admin_list_users_pagination(authenticated_admin_client: AsyncClient):
    resp = await authenticated_admin_client.get("/api/admin/users?page=1&per_page=5")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "users" in body
    assert body["per_page"] == 5
    assert "total" in body


@pytest.mark.asyncio
async def test_admin_list_users_email_search(
    authenticated_admin_client: AsyncClient,
    test_user2,
):
    email = getattr(test_user2, "email", "") or "test2@example.com"
    resp = await authenticated_admin_client.get(
        f"/api/admin/users?search={email.split('@')[0]}"
    )
    assert resp.status_code == 200, resp.text
    users = resp.json().get("users", [])
    assert any(
        (u.get("email") or "").casefold() == email.casefold()
        or u.get("id") == test_user2.id
        for u in users
    )


@pytest.mark.asyncio
async def test_admin_get_user_detail(
    authenticated_admin_client: AsyncClient,
    test_user2,
):
    resp = await authenticated_admin_client.get(f"/api/admin/users/{test_user2.id}")
    assert resp.status_code == 200, resp.text
    user = resp.json().get("user", {})
    assert user.get("id") == test_user2.id
    assert "org_memberships" in user


@pytest.mark.asyncio
async def test_admin_deactivate_and_reactivate(
    authenticated_admin_client: AsyncClient,
    test_user2,
):
    deactivate = await authenticated_admin_client.post(
        f"/api/admin/users/{test_user2.id}/deactivate"
    )
    assert deactivate.status_code == 200, deactivate.text
    assert deactivate.json()["user"]["is_active"] is False

    reactivate = await authenticated_admin_client.post(
        f"/api/admin/users/{test_user2.id}/reactivate"
    )
    assert reactivate.status_code == 200, reactivate.text
    assert reactivate.json()["user"]["is_active"] is True


@pytest.mark.asyncio
async def test_admin_cannot_deactivate_self(
    authenticated_admin_client: AsyncClient,
    test_user,
):
    user_id = getattr(test_user, "user_id", None) or getattr(test_user, "id", "")
    resp = await authenticated_admin_client.post(
        f"/api/admin/users/{user_id}/deactivate"
    )
    assert resp.status_code in (400, 403), resp.text


@pytest.mark.asyncio
async def test_admin_deletion_preview(
    authenticated_admin_client: AsyncClient,
    test_user2,
):
    resp = await authenticated_admin_client.get(
        f"/api/admin/users/{test_user2.id}/deletion-preview"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "can_delete" in body
    assert body.get("target_user_id") == test_user2.id


@pytest.mark.asyncio
async def test_admin_delete_requires_confirm_email(
    authenticated_admin_client: AsyncClient,
    test_user2,
):
    resp = await authenticated_admin_client.post(
        f"/api/admin/users/{test_user2.id}/delete",
        json={"confirm_email": "wrong@example.com"},
    )
    assert resp.status_code == 400, resp.text


@pytest.mark.asyncio
async def test_admin_workspaces_list(authenticated_admin_client: AsyncClient):
    resp = await authenticated_admin_client.get("/api/admin/workspaces")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "workspaces" in body


@pytest.mark.asyncio
async def test_admin_apps_and_tracks_list(authenticated_admin_client: AsyncClient):
    apps = await authenticated_admin_client.get("/api/admin/apps")
    assert apps.status_code == 200, apps.text
    assert "items" in apps.json()

    tracks = await authenticated_admin_client.get("/api/admin/tracks")
    assert tracks.status_code == 200, tracks.text
    assert "items" in tracks.json()
