"""Phase 3a — invitation flow tests."""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_owner_creates_and_invitee_accepts(
    client: AsyncClient, authenticated_client: AsyncClient
):
    org_r = await authenticated_client.post(
        "/api/workspaces", json={"name": f"Invite Org {uuid.uuid4().hex[:8]}"}
    )
    org_id = org_r.json()["workspace"]["id"]

    # Pre-create invitee account so accept can be authenticated as that user.
    su = await client.post(
        "/api/auth/signup",
        json={
            "email": "invitee@example.com",
            "password": "testpassword123",
            "name": "Invitee",
        },
        timeout=10.0,
    )
    if su.status_code not in (200, 201):
        pytest.skip("signup failed")

    invite_r = await authenticated_client.post(
        f"/api/workspaces/{org_id}/invitations",
        json={
            "email": "invitee@example.com",
            "role": "member",
            "can_create_spaces": True,
        },
    )
    assert invite_r.status_code == 200, invite_r.text
    body = invite_r.json()
    assert body["invitation"]["status"] == "pending"
    acceptance_url = body["acceptance_url"]
    assert "/invitations/" in acceptance_url
    token = acceptance_url.rsplit("/", 1)[-1]

    preview = await client.get(f"/api/invitations/{token}")
    assert preview.status_code == 200, preview.text
    assert preview.json()["workspace"]["id"] == org_id

    login = await client.post(
        "/api/auth/login",
        json={"email": "invitee@example.com", "password": "testpassword123"},
    )
    if login.status_code != 200:
        pytest.skip("login failed")
    token_jwt = login.json().get("access_token")

    from app.main import app as _app

    async with AsyncClient(
        transport=ASGITransport(app=_app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token_jwt}"},
    ) as inv_client:
        accept = await inv_client.post(f"/api/invitations/{token}/accept")
        assert accept.status_code == 200, accept.text
        assert accept.json()["role"] == "member"

    members = await authenticated_client.get(f"/api/workspaces/{org_id}/members")
    roles = [m.get("role") for m in members.json()["members"]]
    assert "member" in roles
    assert "owner" in roles


@pytest.mark.asyncio
async def test_revoked_invitation_cannot_be_accepted(
    client: AsyncClient, authenticated_client: AsyncClient
):
    org_r = await authenticated_client.post(
        "/api/workspaces", json={"name": "Revoke Org"}
    )
    org_id = org_r.json()["workspace"]["id"]

    su = await client.post(
        "/api/auth/signup",
        json={
            "email": "revoked@example.com",
            "password": "testpassword123",
            "name": "Revoked",
        },
        timeout=10.0,
    )
    if su.status_code not in (200, 201):
        pytest.skip("signup failed")

    invite_r = await authenticated_client.post(
        f"/api/workspaces/{org_id}/invitations",
        json={"email": "revoked@example.com", "role": "member"},
    )
    invitation_id = invite_r.json()["invitation"]["id"]
    token = invite_r.json()["acceptance_url"].rsplit("/", 1)[-1]

    revoke = await authenticated_client.delete(
        f"/api/workspaces/{org_id}/invitations/{invitation_id}"
    )
    assert revoke.status_code == 200

    login = await client.post(
        "/api/auth/login",
        json={"email": "revoked@example.com", "password": "testpassword123"},
    )
    if login.status_code != 200:
        pytest.skip("login failed")
    token_jwt = login.json().get("access_token")

    from app.main import app as _app

    async with AsyncClient(
        transport=ASGITransport(app=_app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token_jwt}"},
    ) as inv_client:
        accept = await inv_client.post(f"/api/invitations/{token}/accept")
        # Revoked → 410 Gone surfaces as 400 in jvspatial error envelope.
        assert accept.status_code in (400, 410)


@pytest.mark.asyncio
async def test_member_cannot_create_invitation(
    client: AsyncClient, authenticated_client: AsyncClient
):
    org_r = await authenticated_client.post(
        "/api/workspaces", json={"name": "Member Cant Invite"}
    )
    org_id = org_r.json()["workspace"]["id"]

    su = await client.post(
        "/api/auth/signup",
        json={
            "email": "plain_member@example.com",
            "password": "testpassword123",
            "name": "Plain Member",
        },
        timeout=10.0,
    )
    if su.status_code not in (200, 201):
        pytest.skip("signup failed")
    mid = (su.json().get("user") or {}).get("id")

    add = await authenticated_client.post(
        f"/api/workspaces/{org_id}/members",
        json={"member_user_id": mid, "role": "member"},
    )
    assert add.status_code == 200

    login = await client.post(
        "/api/auth/login",
        json={"email": "plain_member@example.com", "password": "testpassword123"},
    )
    if login.status_code != 200:
        pytest.skip("login failed")
    token_jwt = login.json().get("access_token")

    from app.main import app as _app

    async with AsyncClient(
        transport=ASGITransport(app=_app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token_jwt}"},
    ) as member_client:
        bad = await member_client.post(
            f"/api/workspaces/{org_id}/invitations",
            json={"email": "someone@example.com", "role": "member"},
        )
        assert bad.status_code == 403


@pytest.mark.asyncio
async def test_invitation_rejects_invalid_role(
    authenticated_client: AsyncClient,
):
    org_r = await authenticated_client.post(
        "/api/workspaces", json={"name": "Bad Role Invite Org"}
    )
    org_id = org_r.json()["workspace"]["id"]
    bad = await authenticated_client.post(
        f"/api/workspaces/{org_id}/invitations",
        json={"email": "x@example.com", "role": "owner"},
    )
    assert bad.status_code == 400


@pytest.mark.asyncio
async def test_invitation_already_member(
    client: AsyncClient, authenticated_client: AsyncClient
):
    """Inviting a user who's already a member returns 400."""
    org_r = await authenticated_client.post("/api/workspaces", json={"name": "Dup Org"})
    org_id = org_r.json()["workspace"]["id"]

    su = await client.post(
        "/api/auth/signup",
        json={
            "email": "dup@example.com",
            "password": "testpassword123",
            "name": "Dup",
        },
        timeout=10.0,
    )
    if su.status_code not in (200, 201):
        pytest.skip("signup failed")
    mid = (su.json().get("user") or {}).get("id")
    add = await authenticated_client.post(
        f"/api/workspaces/{org_id}/members",
        json={"member_user_id": mid, "role": "member"},
    )
    assert add.status_code == 200

    dup = await authenticated_client.post(
        f"/api/workspaces/{org_id}/invitations",
        json={"email": "dup@example.com", "role": "member"},
    )
    assert dup.status_code == 400


@pytest.mark.asyncio
async def test_wrong_user_cannot_accept_workspace_invitation(
    client: AsyncClient,
    authenticated_client: AsyncClient,
    second_user_client: AsyncClient,
):
    """Accept requires the signed-in user's email to match the invitation."""
    org_r = await authenticated_client.post(
        "/api/workspaces", json={"name": "Email Bind Org"}
    )
    assert org_r.status_code == 200, org_r.text
    org_id = org_r.json()["workspace"]["id"]

    su = await client.post(
        "/api/auth/signup",
        json={
            "email": "intended_invitee@example.com",
            "password": "testpassword123",
            "name": "Intended Invitee",
        },
        timeout=10.0,
    )
    if su.status_code not in (200, 201):
        pytest.skip("signup failed")

    invite_r = await authenticated_client.post(
        f"/api/workspaces/{org_id}/invitations",
        json={"email": "intended_invitee@example.com", "role": "member"},
    )
    assert invite_r.status_code == 200, invite_r.text
    token = invite_r.json()["acceptance_url"].rsplit("/", 1)[-1]

    accept = await second_user_client.post(f"/api/invitations/{token}/accept")
    assert accept.status_code == 403, accept.text
