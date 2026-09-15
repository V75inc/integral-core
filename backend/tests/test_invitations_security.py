"""Invitation security and authorization HTTP tests."""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_guest_cannot_list_workspace_invitations(
    authenticated_client: AsyncClient,
    second_user_client: AsyncClient,
    test_user2,
):
    org_name = f"Guest List Org {uuid.uuid4().hex[:8]}"
    org_r = await authenticated_client.post("/api/workspaces", json={"name": org_name})
    assert org_r.status_code == 200, org_r.text
    org_id = org_r.json()["workspace"]["id"]

    add_guest = await authenticated_client.post(
        f"/api/workspaces/{org_id}/members",
        json={"member_user_id": test_user2.id, "role": "guest"},
    )
    assert add_guest.status_code == 200, add_guest.text

    invite_r = await authenticated_client.post(
        f"/api/workspaces/{org_id}/invitations",
        json={"email": "pending@example.com", "role": "member"},
    )
    assert invite_r.status_code == 200, invite_r.text

    forbidden = await second_user_client.get(f"/api/workspaces/{org_id}/invitations")
    assert forbidden.status_code == 403, forbidden.text

    allowed = await authenticated_client.get(f"/api/workspaces/{org_id}/invitations")
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["total"] >= 1


@pytest.mark.asyncio
async def test_decline_requires_matching_email(
    client: AsyncClient,
    authenticated_client: AsyncClient,
    second_user_client: AsyncClient,
):
    org_name = f"Decline Org {uuid.uuid4().hex[:8]}"
    org_r = await authenticated_client.post("/api/workspaces", json={"name": org_name})
    assert org_r.status_code == 200, org_r.text
    org_id = org_r.json()["workspace"]["id"]

    su = await client.post(
        "/api/auth/signup",
        json={
            "email": "decline_target@example.com",
            "password": "testpassword123",
            "name": "Decline Target",
        },
        timeout=10.0,
    )
    if su.status_code not in (200, 201):
        pytest.skip("signup failed")

    invite_r = await authenticated_client.post(
        f"/api/workspaces/{org_id}/invitations",
        json={"email": "decline_target@example.com", "role": "member"},
    )
    assert invite_r.status_code == 200, invite_r.text
    token = invite_r.json()["acceptance_url"].rsplit("/", 1)[-1]

    unauth = await client.post(f"/api/invitations/{token}/decline")
    assert unauth.status_code == 401, unauth.text

    wrong = await second_user_client.post(f"/api/invitations/{token}/decline")
    assert wrong.status_code == 403, wrong.text

    login = await client.post(
        "/api/auth/login",
        json={"email": "decline_target@example.com", "password": "testpassword123"},
    )
    if login.status_code != 200:
        pytest.skip("login failed")
    token_jwt = login.json().get("access_token")

    from app.main import app as _app

    async with AsyncClient(
        transport=ASGITransport(app=_app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token_jwt}"},
    ) as target_client:
        ok = await target_client.post(f"/api/invitations/{token}/decline")
        assert ok.status_code == 200, ok.text
        assert ok.json()["invitation"]["status"] == "declined"


@pytest.mark.asyncio
async def test_wrong_user_cannot_accept_workspace_invitation_http(
    authenticated_client: AsyncClient,
    second_user_client: AsyncClient,
):
    org_name = f"Accept Authz {uuid.uuid4().hex[:8]}"
    org_r = await authenticated_client.post("/api/workspaces", json={"name": org_name})
    assert org_r.status_code == 200, org_r.text
    org_id = org_r.json()["workspace"]["id"]

    invite_r = await authenticated_client.post(
        f"/api/workspaces/{org_id}/invitations",
        json={"email": "intended_invitee@example.com", "role": "member"},
    )
    assert invite_r.status_code == 200, invite_r.text
    token = invite_r.json()["acceptance_url"].rsplit("/", 1)[-1]

    forbidden = await second_user_client.post(f"/api/invitations/{token}/accept")
    assert forbidden.status_code == 403, forbidden.text


@pytest.mark.asyncio
async def test_invitation_preview_masks_email(
    client: AsyncClient, authenticated_client: AsyncClient
):
    org_name = f"Preview Mask {uuid.uuid4().hex[:8]}"
    org_r = await authenticated_client.post("/api/workspaces", json={"name": org_name})
    assert org_r.status_code == 200, org_r.text
    org_id = org_r.json()["workspace"]["id"]

    invite_r = await authenticated_client.post(
        f"/api/workspaces/{org_id}/invitations",
        json={"email": "secret.person@example.com", "role": "member"},
    )
    assert invite_r.status_code == 200, invite_r.text
    token = invite_r.json()["acceptance_url"].rsplit("/", 1)[-1]

    preview = await client.get(f"/api/invitations/{token}")
    assert preview.status_code == 200, preview.text
    email = preview.json()["invitation"]["email"]
    assert "secret.person" not in email
    assert email.endswith("@example.com")
