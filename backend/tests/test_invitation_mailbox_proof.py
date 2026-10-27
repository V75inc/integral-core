"""By-ID invitations require proof of the current mailbox (AC-01)."""

import hashlib
import uuid
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.models.nodes import User

pytestmark = pytest.mark.smoke


async def invitee_account(client, owner_client, monkeypatch):
    from app.main import app

    email = f"mailbox-{uuid.uuid4().hex}@example.com"
    code = "864209"
    monkeypatch.setattr(
        "app.services.email_verification._generate_otp",
        lambda: (code, hashlib.sha256(code.encode()).hexdigest()),
    )
    monkeypatch.setattr("app.services.email_verification.send_email", AsyncMock())
    signup = await client.post(
        "/api/auth/signup",
        json={"email": email, "password": "testpassword123", "name": "Mailbox"},
    )
    assert signup.status_code == 200, signup.text
    workspace = await owner_client.post(
        "/api/workspaces", json={"name": "Mailbox proof"}
    )
    assert workspace.status_code == 200, workspace.text
    workspace_id = workspace.json()["workspace"]["id"]
    invitation = await owner_client.post(
        f"/api/workspaces/{workspace_id}/invitations",
        json={"email": email, "role": "admin"},
    )
    assert invitation.status_code == 200, invitation.text
    caller = AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {signup.json()['access_token']}"},
    )
    return caller, invitation.json(), workspace_id, code


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["accept", "decline"])
async def test_unverified_by_id_cannot_discover_or_change_invite(
    client, authenticated_client, monkeypatch, action
):
    caller, issued, workspace_id, _ = await invitee_account(
        client, authenticated_client, monkeypatch
    )
    async with caller:
        assert (await caller.get("/api/auth/me")).json()["user"][
            "email_verified"
        ] is False
        listed = await caller.get("/api/me/invitations")
        assert listed.status_code == 200
        assert listed.json()["invitations"] == []
        invitation_id = issued["invitation"]["id"]
        denied = await caller.post(f"/api/me/invitations/{invitation_id}/{action}")
        assert denied.status_code == 403, denied.text
        from app.models.nodes import Invitation
        from app.services.workspace_permissions import can_access_workspace

        invitation = await Invitation.get(invitation_id)
        assert invitation.status == "pending"
        users = await User.find(
            {
                "context.user_id": (await caller.get("/api/auth/me")).json()["user"][
                    "user_id"
                ]
            }
        )
        assert await can_access_workspace(users[0].id, workspace_id) == "none"


@pytest.mark.asyncio
async def test_verified_current_mailbox_discovers_and_accepts(
    client, authenticated_client, monkeypatch
):
    caller, issued, workspace_id, code = await invitee_account(
        client, authenticated_client, monkeypatch
    )
    async with caller:
        verified = await caller.post("/api/auth/verify-email", json={"code": code})
        assert verified.status_code == 200, verified.text
        invitation_id = issued["invitation"]["id"]
        assert any(
            row["id"] == invitation_id
            for row in (await caller.get("/api/me/invitations")).json()["invitations"]
        )
        accepted = await caller.post(f"/api/me/invitations/{invitation_id}/accept")
        assert accepted.status_code == 200, accepted.text
        from app.services.workspace_permissions import can_access_workspace

        me = (await caller.get("/api/auth/me")).json()["user"]
        assert await can_access_workspace(me["user_id"], workspace_id) == "admin"


@pytest.mark.asyncio
@pytest.mark.parametrize("verify_first", [False, True])
async def test_email_change_invalidates_old_proof_and_pending_code(
    client, authenticated_client, monkeypatch, verify_first
):
    from jvspatial.api.auth.models import User as AuthUser

    caller, issued, _, code = await invitee_account(
        client, authenticated_client, monkeypatch
    )
    async with caller:
        me = (await caller.get("/api/auth/me")).json()["user"]
        if verify_first:
            assert (
                await caller.post("/api/auth/verify-email", json={"code": code})
            ).status_code == 200
        auth_user = await AuthUser.get(me["user_id"])
        auth_user.email = f"changed-{uuid.uuid4().hex}@example.com"
        await auth_user.save()
        assert (await caller.get("/api/auth/me")).json()["user"][
            "email_verified"
        ] is False
        assert (await caller.get("/api/me/invitations")).json()["invitations"] == []
        denied = await caller.post(
            f"/api/me/invitations/{issued['invitation']['id']}/accept"
        )
        assert denied.status_code == 403
        assert (
            await caller.post("/api/auth/verify-email", json={"code": code})
        ).status_code == 400
        resent = await caller.post("/api/auth/resend-verification")
        assert resent.status_code == 200
        assert (
            await caller.post("/api/auth/verify-email", json={"code": code})
        ).status_code == 200


@pytest.mark.asyncio
async def test_invitation_token_does_not_mark_account_mailbox_verified(
    client, authenticated_client, monkeypatch
):
    caller, issued, _, _ = await invitee_account(
        client, authenticated_client, monkeypatch
    )
    async with caller:
        token = issued["acceptance_url"].rsplit("/", 1)[-1]
        accepted = await caller.post(f"/api/invitations/{token}/accept")
        assert accepted.status_code == 200, accepted.text
        assert (await caller.get("/api/auth/me")).json()["user"][
            "email_verified"
        ] is False


@pytest.mark.asyncio
async def test_legacy_unbound_boolean_requires_mailbox_reverification(
    client, authenticated_client, monkeypatch
):
    caller, _, _, code = await invitee_account(
        client, authenticated_client, monkeypatch
    )
    async with caller:
        me = (await caller.get("/api/auth/me")).json()["user"]
        user = (await User.find({"context.user_id": me["user_id"]}))[0]
        user.email_verified = True
        await user.save()
        assert (await caller.get("/api/me/invitations")).json()["invitations"] == []
        # The signup code is still usable during the short resend cooldown.
        assert (await caller.post("/api/auth/resend-verification")).status_code == 429
        assert (
            await caller.post("/api/auth/verify-email", json={"code": code})
        ).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/events", "/api/audit-log"])
async def test_account_event_snapshots_cannot_publish_mailbox_otp_hash(
    client, authenticated_client, monkeypatch, path
):
    caller, _, _, code = await invitee_account(
        client, authenticated_client, monkeypatch
    )
    async with caller:
        me = (await caller.get("/api/auth/me")).json()["user"]
        response = await caller.get(path, params={"scope": f"user:{me['id']}"})
        assert response.status_code == 200, response.text
        events = response.json()["events"]
        assert any(event["action"] == "user.create" for event in events)
        assert hashlib.sha256(code.encode()).hexdigest() not in repr(events)
        assert "email_verification" not in repr(events)
