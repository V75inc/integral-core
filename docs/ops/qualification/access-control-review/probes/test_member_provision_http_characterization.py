"""Actual HTTP characterization of remaining member-provision grant admission."""

import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from app.models.edges import IS_MEMBER_OF
from app.services.email_identity import verified_current_email
from app.services.permissions import get_user_node
from httpx import ASGITransport, AsyncClient
from jvspatial.core.context import get_default_context
from tests.test_invitation_mailbox_proof import invitee_account


async def new_recipient(client, monkeypatch, verified):
    from app.main import app

    monkeypatch.setattr("app.services.email_verification.send_email", AsyncMock())
    email = "provision-characterization-" + uuid.uuid4().hex + "@example.com"
    signup = await client.post(
        "/api/auth/signup",
        json={
            "email": email,
            "password": "testpassword123",
            "name": "Synthetic provision recipient",
        },
    )
    assert signup.status_code == 200
    caller = AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": "Bearer " + signup.json()["access_token"]},
    )
    if verified:
        result = await caller.post("/api/auth/verify-email", json={"code": "864209"})
        assert result.status_code == 200, result.text
    me = (await caller.get("/api/auth/me")).json()["user"]
    user = await get_user_node(me["id"])
    assert user is not None
    await caller.aclose()
    fresh = AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": "Bearer " + signup.json()["access_token"]},
    )
    return fresh, user, email


@pytest.mark.asyncio
async def test_owner_email_provision_cannot_bind_unverified_existing_account(
    client, authenticated_client, monkeypatch
):
    monkeypatch.setattr("app.services.member_provision.send_email", AsyncMock())
    workspace = await authenticated_client.post(
        "/api/workspaces", json={"name": "Synthetic provision proof"}
    )
    assert workspace.status_code == 200
    wid = workspace.json()["workspace"]["id"]
    caller, user, email = await new_recipient(client, monkeypatch, False)
    async with caller:
        proof = await verified_current_email(user)
        assert proof is None
        response = await authenticated_client.post(
            "/api/workspaces/" + wid + "/members/provision",
            headers={"X-Integral-Scope": "ws:" + wid},
            json={
                "email": email,
                "display_name": "Synthetic existing account",
                "role": "admin",
                "send_credentials": False,
            },
        )
        rows = await get_default_context().database.find(
            "edge",
            {"source": user.id, "target": wid, "entity": IS_MEMBER_OF._entity_name()},
            limit=2,
        )
        receipt = {
            "http_status": response.status_code,
            "current_verified_mailbox_proof": False,
            "resulting_roles": [r["context"].get("role") for r in rows],
            "scope": "Authorized workspace-owner email-based provisioning of an existing unverified account; no ordinary-user notification forgery or independent-browser claim.",
        }
        Path("/tmp/integral-member-provision-unverified-email-gap.json").write_text(
            json.dumps(receipt, indent=2)
        )
        assert rows == [], receipt


@pytest.mark.asyncio
async def test_workspace_admin_provision_cannot_delegate_owner_only_admin_role(
    client, authenticated_client, monkeypatch
):
    monkeypatch.setattr("app.services.member_provision.send_email", AsyncMock())
    monkeypatch.setattr("app.services.invitations.send_email", AsyncMock())
    admin, issued, wid, code = await invitee_account(
        client, authenticated_client, monkeypatch
    )
    async with admin:
        assert (
            await admin.post("/api/auth/verify-email", json={"code": code})
        ).status_code == 200
        accepted = await admin.post(
            "/api/me/invitations/" + issued["invitation"]["id"] + "/accept"
        )
        assert accepted.status_code == 200, accepted.text
        admin_user = await get_user_node(
            (await admin.get("/api/auth/me")).json()["user"]["id"]
        )
        from app.services.workspace_permissions import can_access_workspace

        assert await can_access_workspace(admin_user.id, wid) == "admin"
        caller, user, email = await new_recipient(client, monkeypatch, True)
        async with caller:
            assert await verified_current_email(user) == email
            response = await admin.post(
                "/api/workspaces/" + wid + "/members/provision",
                headers={"X-Integral-Scope": "ws:" + wid},
                json={
                    "email": email,
                    "display_name": "Synthetic verified account",
                    "role": "admin",
                    "send_credentials": False,
                },
            )
            rows = await get_default_context().database.find(
                "edge",
                {
                    "source": user.id,
                    "target": wid,
                    "entity": IS_MEMBER_OF._entity_name(),
                },
                limit=2,
            )
            receipt = {
                "http_status": response.status_code,
                "actor_workspace_role": "admin",
                "recipient_current_verified_mailbox_proof": True,
                "resulting_roles": [r["context"].get("role") for r in rows],
                "scope": "Registered actor accepted owner-issued admin invitation. Existing recipient separately completed OTP; isolates admin delegation ceiling from mailbox proof.",
            }
            Path("/tmp/integral-member-provision-admin-ceiling-gap.json").write_text(
                json.dumps(receipt, indent=2)
            )
            assert response.status_code == 403 and rows == [], receipt
