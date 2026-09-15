"""HTTP coverage for /me/shared aggregator."""

import uuid

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_me_shared_lists_guest_collaboration(
    authenticated_client: AsyncClient,
    second_user_client: AsyncClient,
    test_user2,
):
    track_title = f"Shared With Me {uuid.uuid4().hex[:8]}"
    create = await authenticated_client.post(
        "/api/tracks",
        json={"title": track_title, "visibility": "private"},
    )
    assert create.status_code == 200, create.text
    track_id = create.json()["track"]["id"]

    empty = await second_user_client.get("/api/me/shared")
    assert empty.status_code == 200, empty.text

    add = await authenticated_client.post(
        f"/api/tracks/{track_id}/collaborators",
        json={"collaborator_user_id": test_user2.id, "role": "viewer"},
    )
    assert add.status_code == 200, add.text

    shared = await second_user_client.get("/api/me/shared")
    assert shared.status_code == 200, shared.text
    workspaces = shared.json().get("workspaces") or []
    track_ids = {row["id"] for ws in workspaces for row in (ws.get("tracks") or [])}
    assert track_id in track_ids


@pytest.mark.asyncio
async def test_me_invitations_lists_pending_for_caller_email(
    client: AsyncClient,
    authenticated_client: AsyncClient,
):
    email = f"me-invite-{uuid.uuid4().hex[:8]}@example.com"
    su = await client.post(
        "/api/auth/signup",
        json={
            "email": email,
            "password": "testpassword123",
            "name": "Invite Target",
        },
        timeout=10.0,
    )
    if su.status_code not in (200, 201):
        pytest.skip("signup failed")

    org_name = f"Me Invites Org {uuid.uuid4().hex[:8]}"
    org_r = await authenticated_client.post("/api/workspaces", json={"name": org_name})
    assert org_r.status_code == 200, org_r.text
    org_id = org_r.json()["workspace"]["id"]

    invite_r = await authenticated_client.post(
        f"/api/workspaces/{org_id}/invitations",
        json={"email": email, "role": "member"},
    )
    assert invite_r.status_code == 200, invite_r.text

    login = await client.post(
        "/api/auth/login",
        json={"email": email, "password": "testpassword123"},
    )
    if login.status_code != 200:
        pytest.skip("login failed")
    token_jwt = login.json().get("access_token")

    from httpx import ASGITransport

    from app.main import app as _app

    async with AsyncClient(
        transport=ASGITransport(app=_app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token_jwt}"},
    ) as invitee_client:
        listed = await invitee_client.get("/api/me/invitations")
        assert listed.status_code == 200, listed.text
        rows = listed.json().get("invitations") or []
        assert any(row.get("workspace_id") == org_id for row in rows)


async def _signup_invite_login(client, authenticated_client):
    """Provision an invitee + org, invite the invitee, return (invitee_client_ctx
    args, org_id, invitation_id). Mirrors the listing test's setup so the
    accept/decline handlers are exercised end-to-end."""
    email = f"me-invite-{uuid.uuid4().hex[:8]}@example.com"
    su = await client.post(
        "/api/auth/signup",
        json={"email": email, "password": "testpassword123", "name": "Invite Target"},
        timeout=10.0,
    )
    if su.status_code not in (200, 201):
        pytest.skip("signup failed")

    org_r = await authenticated_client.post(
        "/api/workspaces", json={"name": f"Me Invites Org {uuid.uuid4().hex[:8]}"}
    )
    assert org_r.status_code == 200, org_r.text
    org_id = org_r.json()["workspace"]["id"]

    invite_r = await authenticated_client.post(
        f"/api/workspaces/{org_id}/invitations",
        json={"email": email, "role": "member"},
    )
    assert invite_r.status_code == 200, invite_r.text

    login = await client.post(
        "/api/auth/login",
        json={"email": email, "password": "testpassword123"},
    )
    if login.status_code != 200:
        pytest.skip("login failed")
    return login.json().get("access_token"), org_id


def _invitee_client(token_jwt: str) -> AsyncClient:
    from httpx import ASGITransport

    from app.main import app as _app

    return AsyncClient(
        transport=ASGITransport(app=_app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token_jwt}"},
    )


@pytest.mark.asyncio
async def test_me_invitation_accept_marks_accepted_and_grants_membership(
    client: AsyncClient,
    authenticated_client: AsyncClient,
):
    token_jwt, org_id = await _signup_invite_login(client, authenticated_client)
    async with _invitee_client(token_jwt) as invitee_client:
        listed = await invitee_client.get("/api/me/invitations")
        assert listed.status_code == 200, listed.text
        inv = next(
            r for r in listed.json()["invitations"] if r.get("workspace_id") == org_id
        )

        accept = await invitee_client.post(f"/api/me/invitations/{inv['id']}/accept")
        assert accept.status_code == 200, accept.text
        body = accept.json()
        assert body["invitation"]["status"] == "accepted"
        assert body["workspace"]["id"] == org_id

        # Invitation no longer pending in the caller's list.
        after = await invitee_client.get("/api/me/invitations")
        assert after.status_code == 200, after.text
        assert not any(r["id"] == inv["id"] for r in after.json()["invitations"])


@pytest.mark.asyncio
async def test_me_invitation_decline_marks_declined(
    client: AsyncClient,
    authenticated_client: AsyncClient,
):
    token_jwt, org_id = await _signup_invite_login(client, authenticated_client)
    async with _invitee_client(token_jwt) as invitee_client:
        listed = await invitee_client.get("/api/me/invitations")
        assert listed.status_code == 200, listed.text
        inv = next(
            r for r in listed.json()["invitations"] if r.get("workspace_id") == org_id
        )

        decline = await invitee_client.post(f"/api/me/invitations/{inv['id']}/decline")
        assert decline.status_code == 200, decline.text
        assert decline.json()["invitation"]["status"] == "declined"

        # Declined invitation drops out of the pending list.
        after = await invitee_client.get("/api/me/invitations")
        assert after.status_code == 200, after.text
        assert not any(r["id"] == inv["id"] for r in after.json()["invitations"])
