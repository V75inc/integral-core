"""Provisioning shares canonical membership admission across HTTP and service."""

import uuid
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from jvspatial.core.context import get_default_context

from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    ResourceConflictError,
)
from app.models.edges import IS_MEMBER_OF
from app.models.nodes import Workspace
from app.services import member_provision as provision
from app.services import workspace_membership as membership
from app.services.email_identity import verified_current_email
from app.services.permissions import get_user_node
from tests.test_invitation_mailbox_proof import invitee_account

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio]


async def setup_accounts(client, owner_client, monkeypatch, verified=True, admin=False):
    monkeypatch.setattr("app.services.email_delivery.enqueue_email", AsyncMock())
    monkeypatch.setattr(
        "app.services.member_provision.send_email", AsyncMock(return_value=True)
    )
    caller, issued, wid, code = await invitee_account(client, owner_client, monkeypatch)
    if verified:
        assert (
            await caller.post("/api/auth/verify-email", json={"code": code})
        ).status_code == 200
    if admin:
        accepted = await caller.post(
            f"/api/me/invitations/{issued['invitation']['id']}/accept"
        )
        assert accepted.status_code == 200, accepted.text
    recipient = await get_user_node(
        (await caller.get("/api/auth/me")).json()["user"]["id"]
    )
    owner = await get_user_node(
        (await owner_client.get("/api/auth/me")).json()["user"]["id"]
    )
    from app.main import app

    headers = dict(caller.headers)
    await caller.aclose()
    caller = AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers=headers
    )
    return (
        caller,
        owner,
        recipient,
        await Workspace.find_one({"id": wid}),
        issued["invitation"]["email"],
    )


async def grants(user, workspace):
    return await get_default_context().database.find(
        "edge",
        {
            "source": user.id,
            "target": workspace.id,
            "entity": IS_MEMBER_OF._entity_name(),
        },
        limit=2,
    )


@pytest.mark.parametrize("surface", ["http", "service"])
async def test_existing_unverified_account_cannot_receive_email_matched_grant(
    client, authenticated_client, monkeypatch, surface
):
    caller, owner, recipient, workspace, email = await setup_accounts(
        client, authenticated_client, monkeypatch, verified=False
    )
    async with caller:
        assert await verified_current_email(recipient) is None
        if surface == "service":
            with pytest.raises(InsufficientPermissionsError):
                await provision.provision_workspace_member(
                    workspace=workspace,
                    actor_id=owner.id,
                    email=email,
                    display_name="Unverified",
                    role="admin",
                    send_credentials=False,
                )
        else:
            denied = await authenticated_client.post(
                f"/api/workspaces/{workspace.id}/members/provision",
                json={
                    "email": email,
                    "display_name": "Unverified",
                    "role": "admin",
                    "send_credentials": False,
                },
            )
            assert denied.status_code == 403, denied.text
        assert await grants(recipient, workspace) == []
        provision.send_email.assert_not_awaited()


@pytest.mark.parametrize("surface", ["http", "service"])
async def test_owner_can_provision_verified_existing_account_with_actual_receipt(
    client, authenticated_client, monkeypatch, surface
):
    caller, owner, recipient, workspace, email = await setup_accounts(
        client, authenticated_client, monkeypatch
    )
    async with caller:
        if surface == "service":
            result = await provision.provision_workspace_member(
                workspace=workspace,
                actor_id=owner.user_id,
                email=email,
                display_name="Verified",
                role="admin",
                send_credentials=False,
            )
        else:
            response = await authenticated_client.post(
                f"/api/workspaces/{workspace.id}/members/provision",
                json={
                    "email": email,
                    "display_name": "Verified",
                    "role": "admin",
                    "send_credentials": False,
                },
            )
            assert response.status_code == 200, response.text
            result = response.json()
        rows = await grants(recipient, workspace)
        assert (
            len(rows) == 1
            and rows[0]["context"]["role"] == result["granted_role"] == "admin"
        )
        assert result["created"] is False and result["grant_outcome"] == "created"
        before = rows[0]
        retry = await provision.provision_workspace_member(
            workspace=workspace,
            actor_id=owner.id,
            email=email,
            display_name="Verified",
            role="admin",
            send_credentials=False,
        )
        assert retry["grant_outcome"] == "unchanged"
        assert (await grants(recipient, workspace))[0] == before
        with pytest.raises(ResourceConflictError):
            await provision.provision_workspace_member(
                workspace=workspace,
                actor_id=owner.id,
                email=email,
                display_name="Verified",
                role="member",
                send_credentials=False,
            )
        assert (await grants(recipient, workspace))[0] == before


@pytest.mark.parametrize("role", ["member", "guest", "admin"])
async def test_admin_ceiling_applies_to_new_account_http_and_direct_service(
    client, authenticated_client, monkeypatch, role
):
    caller, _, admin, workspace, _ = await setup_accounts(
        client, authenticated_client, monkeypatch, admin=True
    )
    async with caller:
        email = f"provision-{uuid.uuid4().hex}@example.com"
        if role == "admin":
            with pytest.raises(InsufficientPermissionsError):
                await provision.provision_workspace_member(
                    workspace=workspace,
                    actor_id=admin.id,
                    email=email,
                    display_name="New",
                    role=role,
                    send_credentials=False,
                )
            denied = await caller.post(
                f"/api/workspaces/{workspace.id}/members/provision",
                json={
                    "email": email,
                    "display_name": "New",
                    "role": role,
                    "send_credentials": False,
                },
            )
            assert denied.status_code == 403, denied.text
            assert await provision._find_auth_user_by_email(email) is None
        else:
            result = await caller.post(
                f"/api/workspaces/{workspace.id}/members/provision",
                json={
                    "email": email,
                    "display_name": "New",
                    "role": role,
                    "send_credentials": False,
                },
            )
            assert result.status_code == 200, result.text
            data = result.json()
            recipient = await get_user_node(data["auth_user_id"])
            assert recipient.email_verified is False and not recipient.verified_email
            assert await verified_current_email(recipient) is None
            assert len(await grants(recipient, workspace)) == 1
            assert data["created"] is True and data["granted_role"] == role
            assert data["must_change_password"] is True
            from app.services.resource_location import locate_resource

            personal = await provision.ensure_personal_workspace(recipient)
            assert await locate_resource("workspace", personal.id) is not None
        provision.send_email.assert_not_awaited()


@pytest.mark.parametrize(
    "fields",
    [
        {"role": "owner"},
        {"role": None},
        {"role": True},
        {"role": "ADMIN"},
        {"send_credentials": "false"},
        {"can_create_apps": True},
        {"display_name": " "},
    ],
)
async def test_http_provision_rejects_invalid_or_undeclared_inputs_before_account_creation(
    client, authenticated_client, monkeypatch, fields
):
    caller, _, _, workspace, _ = await setup_accounts(
        client, authenticated_client, monkeypatch
    )
    async with caller:
        email = f"invalid-{uuid.uuid4().hex}@example.com"
        response = await authenticated_client.post(
            f"/api/workspaces/{workspace.id}/members/provision",
            json={
                "email": email,
                "display_name": "New",
                "send_credentials": False,
                **fields,
            },
        )
        assert response.status_code == 400, response.text
        assert await provision._find_auth_user_by_email(email) is None


async def test_service_denies_unauthorized_actor_and_redirected_credentials(
    client, authenticated_client, monkeypatch
):
    caller, owner, recipient, workspace, email = await setup_accounts(
        client, authenticated_client, monkeypatch
    )
    async with caller:
        with pytest.raises(InsufficientPermissionsError):
            await provision.provision_workspace_member(
                workspace=workspace,
                actor_id=recipient.id,
                email=email,
                display_name="Denied",
                send_credentials=False,
            )
        with pytest.raises(BadRequestError):
            await provision.provision_workspace_member(
                workspace=workspace,
                actor_id=owner.id,
                email=email,
                display_name="Denied",
                credentials_email="elsewhere@example.com",
            )
        assert await grants(recipient, workspace) == []
        provision.send_email.assert_not_awaited()


@pytest.mark.postgres
async def test_provision_audit_fault_rolls_back_identity_profile_and_membership(
    client, authenticated_client, monkeypatch
):
    caller, owner, _, workspace, _ = await setup_accounts(
        client, authenticated_client, monkeypatch
    )
    async with caller:
        email = f"rollback-{uuid.uuid4().hex}@example.com"
        original = membership._record_change
        captured = []
        database = get_default_context().database
        personal_before = await database.count(
            "node", {"entity": Workspace._entity_name(), "context.kind": "personal"}
        )

        async def fail(actor, workspace, action, before, after):
            if action == "workspace.member_provision":
                captured.append(after)
                raise RuntimeError("Injected provisioning audit failure")
            return await original(actor, workspace, action, before, after)

        monkeypatch.setattr(membership, "_record_change", fail)
        with pytest.raises(RuntimeError, match="Injected"):
            await provision.provision_workspace_member(
                workspace=workspace,
                actor_id=owner.id,
                email=email,
                display_name="Rollback",
                role="admin",
                send_credentials=True,
            )
        assert await provision._find_auth_user_by_email(email) is None
        assert len(captured) == 1
        recipient_id = captured[0]["member_user_id"]
        assert await database.find("node", {"id": recipient_id}, limit=1) == []
        assert (
            await database.find(
                "edge",
                {"$or": [{"source": recipient_id}, {"target": recipient_id}]},
                limit=1,
            )
            == []
        )
        assert (
            await database.count(
                "node", {"entity": Workspace._entity_name(), "context.kind": "personal"}
            )
            == personal_before
        )
        assert (
            await database.find(
                "object",
                {
                    "entity": "OperationEventOutbox",
                    "context.event.after.member_user_id": recipient_id,
                },
                limit=1,
            )
            == []
        )
        provision.send_email.assert_not_awaited()


@pytest.mark.parametrize(
    "field,value", [("email_verified", "true"), ("verified_email", "other@example.com")]
)
async def test_existing_account_proof_is_exact_primary_state(
    client, authenticated_client, monkeypatch, field, value
):
    caller, owner, recipient, workspace, email = await setup_accounts(
        client, authenticated_client, monkeypatch
    )
    async with caller:
        await get_default_context().database.find_one_and_update(
            "node", {"id": recipient.id}, {"$set": {f"context.{field}": value}}
        )
        from app.exceptions import AuthorizationUnavailableError

        with pytest.raises(
            (InsufficientPermissionsError, AuthorizationUnavailableError)
        ):
            await provision.provision_workspace_member(
                workspace=workspace,
                actor_id=owner.id,
                email=email,
                display_name="Corrupt proof",
                send_credentials=False,
            )
        assert await grants(recipient, workspace) == []


async def test_credential_delivery_observes_committed_account_and_membership(
    client, authenticated_client, monkeypatch
):
    caller, owner, _, workspace, _ = await setup_accounts(
        client, authenticated_client, monkeypatch
    )
    async with caller:
        email = f"delivery-{uuid.uuid4().hex}@example.com"

        async def deliver(message):
            from app.services.entry_write_scope import is_postgres_entry_transaction

            assert not is_postgres_entry_transaction(get_default_context().database)
            identity = await provision._find_auth_user_by_email(email)
            assert identity is not None
            recipient = await get_user_node(identity.id)
            assert len(await grants(recipient, workspace)) == 1
            return True

        monkeypatch.setattr(provision, "send_email", AsyncMock(side_effect=deliver))
        result = await provision.provision_workspace_member(
            workspace=workspace,
            actor_id=owner.id,
            email=email,
            display_name="Delivered",
            role="admin",
        )
        assert result["credentials_emailed"] is True and result["created"] is True
        assert "password" not in result and "temp_password" not in result


async def test_http_provision_rejects_query_fields_and_foreign_scope(
    client, authenticated_client, monkeypatch
):
    caller, _, _, workspace, email = await setup_accounts(
        client, authenticated_client, monkeypatch
    )
    async with caller:
        payload = {
            "email": email,
            "display_name": "Verified",
            "send_credentials": False,
        }
        for suffix, headers in [
            ("?role=admin", {}),
            ("", {"X-Integral-Scope": "malformed"}),
        ]:
            response = await authenticated_client.post(
                f"/api/workspaces/{workspace.id}/members/provision" + suffix,
                json=payload,
                headers=headers,
            )
            assert response.status_code == 400, response.text
