"""Application-proof activation delivery retry (same setup token)."""

import hashlib
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import application_mailbox_verification as mailbox
from app.services.credential_delivery import (
    _activation_failure_category,
    activation_delivery_public_status,
    retry_application_proof_activation_delivery,
)
from app.services.email_service import EmailMessage, _send_via_resend, _SendCredentials
from app.services.permissions import get_user_node


@pytest.mark.asyncio
async def test_application_activation_persists_purpose_tagged_token(
    test_user, monkeypatch
):
    from app.services import credential_delivery as delivery

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "ab" * 32)
    monkeypatch.setattr(delivery, "require_delivery_storage", lambda: None)
    monkeypatch.setattr(delivery, "_fresh_user", AsyncMock(return_value=test_user))
    monkeypatch.setattr(delivery, "_lock_identity", AsyncMock())

    @asynccontextmanager
    async def no_op_scope(_identity):
        yield

    monkeypatch.setattr("app.services.entry_write_scope.entry_write_scope", no_op_scope)
    monkeypatch.setattr(
        delivery,
        "_dispatch_application_proof_send",
        AsyncMock(return_value=(True, "accepted", "message-1")),
    )
    monkeypatch.setattr(
        delivery,
        "_finalize_application_proof_send",
        AsyncMock(return_value={"activation_emailed": True}),
    )

    result = await delivery.deliver_application_proof_setup_link(
        user_node=test_user,
        recipient_email="personal@example.com",
        account_email="work@example.com",
        workspace_id="workspace-1",
        display_name="First Hire",
        actor_user_id="admin-1",
    )

    prefs = test_user.preferences
    token_slot = prefs["reset_token"]
    delivery_slot = prefs["credentials_delivery"]
    assert result["activation_emailed"] is True
    assert token_slot["purpose"] == "account_activation"
    assert token_slot["hash"] == delivery_slot["setup_token_hash"]
    from app.services.credential_crypto import decrypt_secret_from_storage
    from app.services.member_provision import (
        must_change_password,
        set_must_change_password,
    )
    from app.services.password_reset import ERR_INVALID_TOKEN, consume_reset_token

    token = decrypt_secret_from_storage(
        delivery_slot["setup_token_enc"],
        aad=f"credentials-delivery:{test_user.id}:{delivery_slot['intent_id']}",
    )
    rejected, error = await consume_reset_token(token, "A-strong-password-135!2026")
    assert not rejected and error == ERR_INVALID_TOKEN
    set_must_change_password(test_user, True)
    await test_user.save()
    activated, error = await consume_reset_token(
        token,
        "A-strong-password-135!2026",
        expected_purpose="account_activation",
    )
    assert activated and error is None
    refreshed_rows = await type(test_user).find({"id": test_user.id})
    refreshed = refreshed_rows[0] if refreshed_rows else None
    assert refreshed is not None
    assert not must_change_password(refreshed)


@pytest.mark.asyncio
async def test_retry_reuses_setup_token_after_transient_failure(
    client, authenticated_client, monkeypatch
):
    from tests.test_member_provision_authority import setup_accounts

    monkeypatch.setattr(
        "app.services.member_provision.send_email", AsyncMock(return_value=True)
    )
    _caller, owner, _recipient, workspace, _email = await setup_accounts(
        client, authenticated_client, monkeypatch, verified=True, admin=False
    )
    member = await get_user_node(owner.id)
    assert member is not None
    slot = {
        "version": 3,
        "application_proof": True,
        "intent_id": "intent-retry-1",
        "recipient_email": "personal@example.com",
        "account_email": "work@example.com",
        "workspace_id": str(workspace.id),
        "display_name": "Retry Hire",
        "actor_user_id": owner.id,
        "credential_version": "abc",
        "setup_token_enc": "v1:placeholder",
        "setup_token_hash": "hash",
        "expires_at": "2099-01-01T00:00:00+00:00",
        "state": "ready",
    }
    member.preferences = {
        "credentials_delivery": slot,
        "reset_token": {
            "hash": "hash",
            "expires_at": "2099-01-01T00:00:00+00:00",
            "purpose": "account_activation",
        },
    }
    await member.save()

    send_outcomes = [
        (False, "pending_retry", None),
        (True, "accepted", "msg-123"),
    ]

    async def fake_release(*, user_node, slot, setup_token):
        return send_outcomes.pop(0)

    import hashlib

    member.preferences["credentials_delivery"]["credential_version"] = hashlib.sha256(
        b"pw"
    ).hexdigest()
    await member.save()
    with (
        patch(
            "app.services.credential_delivery._release_credentials_email",
            side_effect=fake_release,
        ),
        patch(
            "app.services.credential_crypto.decrypt_secret_from_storage",
            return_value="same-token",
        ),
        patch("app.services.authentication.get_authentication_service") as auth_svc,
    ):
        auth = AsyncMock()
        auth.password_hash = "pw"
        auth.is_active = True
        auth_svc.return_value._get_user_by_id = AsyncMock(return_value=auth)
        first = await retry_application_proof_activation_delivery(
            workspace_id=str(workspace.id),
            member_user_id=member.id,
            actor_user_id=owner.id,
            intent_id="intent-retry-1",
        )
        assert first["state"] in ("pending_retry", "suppressed", "sending")
        second = await retry_application_proof_activation_delivery(
            workspace_id=str(workspace.id),
            member_user_id=member.id,
            actor_user_id=owner.id,
            intent_id="intent-retry-1",
        )
    assert second.get("state") == "already_released" or second.get("activation_emailed")


@pytest.mark.asyncio
async def test_unauthorized_cross_workspace_retry_denied(
    client, authenticated_client, monkeypatch
):
    from tests.test_member_provision_authority import setup_accounts

    monkeypatch.setattr(
        "app.services.member_provision.send_email", AsyncMock(return_value=True)
    )
    _caller, owner, recipient, workspace, _email = await setup_accounts(
        client, authenticated_client, monkeypatch, verified=True, admin=False
    )
    member = await get_user_node(recipient.id)
    assert member is not None
    member.preferences = {
        "credentials_delivery": {
            "version": 3,
            "application_proof": True,
            "intent_id": "intent-x",
            "workspace_id": "n.Workspace.other",
            "recipient_email": "a@example.com",
            "setup_token_enc": "v1:x",
            "expires_at": "2099-01-01T00:00:00+00:00",
            "state": "ready",
        }
    }
    await member.save()
    owner_member = await get_user_node(owner.id)
    assert owner_member is not None
    result = await retry_application_proof_activation_delivery(
        workspace_id=str(workspace.id),
        member_user_id=owner_member.id,
        actor_user_id=owner.id,
        intent_id="intent-x",
    )
    assert result["state"] == "not_pending"


@pytest.mark.asyncio
async def test_mailbox_proof_flow_on_ui_core_base(
    client, authenticated_client, monkeypatch
):
    """Smoke: proof confirm still works on rebased tree."""
    from tests.test_member_provision_authority import setup_accounts

    monkeypatch.setattr(
        "app.services.member_provision.send_email", AsyncMock(return_value=True)
    )
    _caller, owner, _recipient, workspace, _email = await setup_accounts(
        client, authenticated_client, monkeypatch, verified=True, admin=False
    )
    started = await mailbox.start_application_mailbox_verification(
        candidate_entry_id="n.Entry.ui-core",
        workspace_id=str(workspace.id),
        email="personal@example.com",
    )
    ok, _, proof_id = await mailbox.confirm_application_mailbox_verification(
        candidate_entry_id="n.Entry.ui-core",
        code=started["code"],
        proof_id=started["proof_id"],
    )
    assert ok and proof_id


def test_activation_failure_category_never_infers_success_from_status():
    assert (
        _activation_failure_category(success=True, status="failed", error="x")
        == "accepted"
    )
    assert (
        _activation_failure_category(success=False, status="suppressed", error="")
        == "suppressed"
    )
    assert (
        _activation_failure_category(success=False, status=None, error="timeout")
        == "pending_retry"
    )
    assert (
        _activation_failure_category(success=False, status="accepted", error="")
        == "pending_retry"
    )
    assert (
        _activation_failure_category(success=False, status="failed", error="")
        == "failed"
    )


@pytest.mark.asyncio
async def test_resend_suppression_maps_safe_outcome_without_raw_payload(monkeypatch):
    monkeypatch.setattr("app.config.settings.RESEND_API_KEY", "re_test")
    mock_resp = MagicMock()
    mock_resp.status_code = 422
    mock_resp.content = b'{"message":"Email address is on the suppression list"}'
    mock_resp.json.return_value = {
        "message": "Email address is on the suppression list",
        "token": "secret-setup-token",
    }
    client = AsyncMock()
    client.post = AsyncMock(return_value=mock_resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=client):
        outcome = await _send_via_resend(
            EmailMessage(to="a@b.com", subject="Hi", html="<p>x</p>", text="x"),
            _SendCredentials(
                provider="resend",
                from_email="from@x.com",
                from_name="",
                api_key="re_test",
            ),
            reply_to=None,
        )

    assert outcome.success is False
    assert outcome.status == "suppressed"
    assert outcome.error == "Recipient suppressed by mail provider"
    assert "suppression list" not in outcome.error
    assert "secret-setup-token" not in str(outcome)


async def _seed_application_proof_member(
    member, *, workspace_id: str, owner_id: str, intent_id: str = "intent-proof"
):
    token_hash = "hash-stable"
    slot = {
        "version": 3,
        "application_proof": True,
        "intent_id": intent_id,
        "recipient_email": "personal@example.com",
        "account_email": "work@example.com",
        "workspace_id": workspace_id,
        "display_name": "Hire",
        "actor_user_id": owner_id,
        "credential_version": hashlib.sha256(b"pw").hexdigest(),
        "setup_token_enc": "v1:placeholder",
        "setup_token_hash": token_hash,
        "expires_at": "2099-01-01T00:00:00+00:00",
        "state": "ready",
    }
    member.preferences = {
        "credentials_delivery": slot,
        "reset_token": {
            "hash": token_hash,
            "expires_at": "2099-01-01T00:00:00+00:00",
            "purpose": "account_activation",
        },
    }
    await member.save()
    return slot


@pytest.mark.asyncio
async def test_suppression_retry_stays_pending_with_same_token_material(
    client, authenticated_client, monkeypatch
):
    from tests.test_member_provision_authority import setup_accounts

    monkeypatch.setattr(
        "app.services.member_provision.send_email", AsyncMock(return_value=True)
    )
    _caller, owner, _recipient, workspace, _email = await setup_accounts(
        client, authenticated_client, monkeypatch, verified=True, admin=False
    )
    member = await get_user_node(owner.id)
    assert member is not None
    await _seed_application_proof_member(
        member, workspace_id=str(workspace.id), owner_id=owner.id
    )

    with (
        patch(
            "app.services.credential_delivery._release_credentials_email",
            new=AsyncMock(return_value=(False, "suppressed", None)),
        ),
        patch(
            "app.services.credential_crypto.decrypt_secret_from_storage",
            return_value="same-token",
        ),
        patch("app.services.authentication.get_authentication_service") as auth_svc,
    ):
        auth = AsyncMock()
        auth.password_hash = "pw"
        auth.is_active = True
        auth_svc.return_value._get_user_by_id = AsyncMock(return_value=auth)
        result = await retry_application_proof_activation_delivery(
            workspace_id=str(workspace.id),
            member_user_id=member.id,
            actor_user_id=owner.id,
            intent_id="intent-proof",
        )

    assert result["state"] == "suppressed"
    assert result.get("activation_emailed") is False
    assert result.get("delivery_retryable") is True
    refreshed = await get_user_node(member.id)
    assert refreshed is not None
    prefs = refreshed.preferences or {}
    pending = prefs.get("credentials_delivery") or {}
    assert pending.get("setup_token_enc") == "v1:placeholder"
    assert prefs.get("credentials_delivery_released") is None


@pytest.mark.asyncio
async def test_successful_retry_clears_pending_secret_slot(
    client, authenticated_client, monkeypatch
):
    from tests.test_member_provision_authority import setup_accounts

    monkeypatch.setattr(
        "app.services.member_provision.send_email", AsyncMock(return_value=True)
    )
    _caller, owner, _recipient, workspace, _email = await setup_accounts(
        client, authenticated_client, monkeypatch, verified=True, admin=False
    )
    member = await get_user_node(owner.id)
    assert member is not None
    await _seed_application_proof_member(
        member, workspace_id=str(workspace.id), owner_id=owner.id
    )

    with (
        patch(
            "app.services.credential_delivery._release_credentials_email",
            new=AsyncMock(return_value=(True, "accepted", "msg-ok")),
        ),
        patch(
            "app.services.credential_crypto.decrypt_secret_from_storage",
            return_value="same-token",
        ),
        patch("app.services.authentication.get_authentication_service") as auth_svc,
    ):
        auth = AsyncMock()
        auth.password_hash = "pw"
        auth.is_active = True
        auth_svc.return_value._get_user_by_id = AsyncMock(return_value=auth)
        result = await retry_application_proof_activation_delivery(
            workspace_id=str(workspace.id),
            member_user_id=member.id,
            actor_user_id=owner.id,
            intent_id="intent-proof",
        )

    assert result.get("activation_emailed") is True
    refreshed = await get_user_node(member.id)
    assert refreshed is not None
    prefs = refreshed.preferences or {}
    assert not prefs.get("credentials_delivery")
    released = prefs.get("credentials_delivery_released") or {}
    assert released.get("at")
    assert released.get("application_proof") is True
    second = await retry_application_proof_activation_delivery(
        workspace_id=str(workspace.id),
        member_user_id=member.id,
        actor_user_id=owner.id,
        intent_id="intent-proof",
    )
    assert second["state"] == "already_released"


@pytest.mark.asyncio
async def test_concurrent_retry_while_sending_returns_public_sending_state(
    client, authenticated_client, monkeypatch
):
    from tests.test_member_provision_authority import setup_accounts

    monkeypatch.setattr(
        "app.services.member_provision.send_email", AsyncMock(return_value=True)
    )
    _caller, owner, _recipient, workspace, _email = await setup_accounts(
        client, authenticated_client, monkeypatch, verified=True, admin=False
    )
    member = await get_user_node(owner.id)
    assert member is not None
    lease = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    member.preferences = {
        "credentials_delivery": {
            "version": 3,
            "application_proof": True,
            "intent_id": "intent-proof",
            "workspace_id": str(workspace.id),
            "recipient_email": "p@example.com",
            "setup_token_enc": "v1:x",
            "setup_token_hash": "h",
            "expires_at": "2099-01-01T00:00:00+00:00",
            "state": "sending",
            "lease_until": lease,
            "credential_version": hashlib.sha256(b"pw").hexdigest(),
        },
        "reset_token": {
            "hash": "h",
            "expires_at": "2099-01-01T00:00:00+00:00",
            "purpose": "account_activation",
        },
    }
    await member.save()

    with patch("app.services.authentication.get_authentication_service") as auth_svc:
        auth = AsyncMock()
        auth.password_hash = "pw"
        auth.is_active = True
        auth_svc.return_value._get_user_by_id = AsyncMock(return_value=auth)
        result = await retry_application_proof_activation_delivery(
            workspace_id=str(workspace.id),
            member_user_id=member.id,
            actor_user_id=owner.id,
            intent_id="intent-proof",
        )

    assert result["state"] == "sending"
    assert "setup_token" not in str(result)


@pytest.mark.asyncio
async def test_activation_delivery_api_admin_only_and_safe_payload(
    client, authenticated_client, monkeypatch
):
    from tests.test_member_provision_authority import setup_accounts

    monkeypatch.setattr(
        "app.services.member_provision.send_email", AsyncMock(return_value=True)
    )
    member_client, owner, recipient, workspace, _email = await setup_accounts(
        client, authenticated_client, monkeypatch, verified=True, admin=False
    )
    owner_node = await get_user_node(owner.id)
    assert owner_node is not None
    await _seed_application_proof_member(
        owner_node,
        workspace_id=str(workspace.id),
        owner_id=owner.id,
        intent_id="intent-api",
    )
    scope = {"X-Integral-Scope": f"ws:{workspace.id}"}
    admin_resp = await authenticated_client.get(
        f"/api/workspaces/{workspace.id}/members/{owner.id}/activation-delivery",
        headers=scope,
    )
    assert admin_resp.status_code == 200, admin_resp.text
    admin_body = admin_resp.json()
    assert admin_body["state"] in ("pending_retry", "sending", "suppressed")
    assert "setup_token" not in str(admin_body)
    assert "error" not in admin_body

    member_resp = await member_client.get(
        f"/api/workspaces/{workspace.id}/members/{owner.id}/activation-delivery",
        headers=scope,
    )
    assert member_resp.status_code == 403

    with (
        patch(
            "app.services.credential_delivery._release_credentials_email",
            new=AsyncMock(return_value=(False, "suppressed", None)),
        ),
        patch(
            "app.services.credential_crypto.decrypt_secret_from_storage",
            return_value="same-token",
        ),
        patch("app.services.authentication.get_authentication_service") as auth_svc,
    ):
        auth = AsyncMock()
        auth.password_hash = "pw"
        auth.is_active = True
        auth_svc.return_value._get_user_by_id = AsyncMock(return_value=auth)
        retry_resp = await authenticated_client.post(
            f"/api/workspaces/{workspace.id}/members/{owner.id}/activation-delivery/retry",
            headers=scope,
            json={"intent_id": "intent-api"},
        )
    assert retry_resp.status_code == 200, retry_resp.text
    retry_body = retry_resp.json()
    assert retry_body["state"] == "suppressed"
    assert retry_body.get("activation_emailed") is False
    assert "Resend rejected" not in str(retry_body)
