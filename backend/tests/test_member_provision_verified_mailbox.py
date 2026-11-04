"""Member provision with a purpose-bound mailbox verification proof."""

from unittest.mock import AsyncMock, patch

import pytest

from app.services import mailbox_verification as svc


@pytest.mark.asyncio
@pytest.mark.parametrize("unavailable", ["storage", "encryption"])
async def test_verified_mailbox_preflights_delivery_before_creating_an_account(
    client, authenticated_client, monkeypatch, unavailable
):
    from app.api.errors import BadRequestError
    from app.services.member_provision import (
        _find_auth_user_by_email,
        provision_workspace_member,
    )
    from tests.test_member_provision_authority import setup_accounts

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "ab" * 32)
    _caller, owner, _recipient, workspace, _email = await setup_accounts(
        client, authenticated_client, monkeypatch, verified=True, admin=False
    )
    monkeypatch.setattr(
        svc, "proof_ready_for_account_provisioning", AsyncMock(return_value=True)
    )
    if unavailable == "storage":

        def unavailable_storage():
            raise BadRequestError(
                message="Alternate delivery requires transactional shared storage"
            )

        monkeypatch.setattr(
            "app.services.credential_delivery.require_delivery_storage",
            unavailable_storage,
        )
    else:
        monkeypatch.setattr(
            "app.services.credential_crypto.encryption_available", lambda: False
        )
    email = f"missing-{unavailable}@example.com"
    with pytest.raises(BadRequestError):
        await provision_workspace_member(
            workspace=workspace,
            actor_id=owner.id,
            email=email,
            display_name="Preflight test",
            personal_email="personal@example.com",
            mailbox_verification_proof_id="proof-preflight",
            mailbox_subject_entry_id="n.Entry.preflight",
            mailbox_verification_purpose="account_creation",
        )
    assert await _find_auth_user_by_email(email) is None


@pytest.mark.asyncio
async def test_provision_accepts_valid_proof_and_calls_activation_delivery(
    client, authenticated_client, monkeypatch
):
    from app.models.mailbox_verification import MailboxVerificationProof
    from app.models.nodes import User
    from tests.test_member_provision_authority import setup_accounts

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "ab" * 32)
    monkeypatch.setattr(
        "app.services.member_provision.send_email", AsyncMock(return_value=True)
    )
    caller, owner, _recipient, workspace, _email = await setup_accounts(
        client, authenticated_client, monkeypatch, verified=True, admin=False
    )
    async with caller:
        started = await svc.start_mailbox_verification(
            subject_entry_id="n.Entry.proof-hire",
            workspace_id=str(workspace.id),
            email="personal@example.com",
            purpose="account_creation",
        )
        ok, _, proof_id = await svc.confirm_mailbox_verification(
            subject_entry_id="n.Entry.proof-hire",
            workspace_id=str(workspace.id),
            purpose="account_creation",
            code=started["code"],
            proof_id=started["proof_id"],
        )
        assert ok is True

        outbox_deliveries = 0

        async def assert_staged_before_outbox(_outbox_id):
            nonlocal outbox_deliveries
            outbox_deliveries += 1
            if outbox_deliveries == 1:
                return
            proof = await MailboxVerificationProof.get(proof_id)
            assert proof is not None and proof.consumed_at
            from app.services.member_provision import _find_auth_user_by_email
            from app.services.permissions import get_user_node

            auth_user = await _find_auth_user_by_email("new.hire@example.com")
            assert auth_user is not None
            provisioned = await get_user_node(auth_user.id)
            assert provisioned is not None
            slot = provisioned.preferences["credentials_delivery"]
            assert slot["mailbox_verification_proof"] is True
            assert slot["state"] == "sending"

        with (
            patch(
                "app.services.credential_delivery._dispatch_verified_mailbox_activation_send",
                new=AsyncMock(return_value=(True, "accepted", "message-1")),
            ),
            patch(
                "app.services.member_provision.membership._deliver",
                new=AsyncMock(side_effect=assert_staged_before_outbox),
            ),
        ):
            from app.services.member_provision import provision_workspace_member

            result = await provision_workspace_member(
                workspace=workspace,
                actor_id=owner.id,
                email="new.hire@example.com",
                display_name="Proof Hire",
                send_credentials=True,
                personal_email="personal@example.com",
                mailbox_verification_proof_id=proof_id,
                mailbox_subject_entry_id="n.Entry.proof-hire",
                mailbox_verification_purpose="account_creation",
            )
        assert result["created"] is True
        assert result["credentials_activation_emailed"] is True
        assert outbox_deliveries == 2
        provisioned = await User.get(result["user_id"])
        assert provisioned is not None
        assert provisioned.preferences["credentials_delivery_released"]


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_failed_proof_consumption_rolls_back_member_and_activation_slot(
    client, authenticated_client, monkeypatch
):
    from app.api.errors import BadRequestError
    from app.models.mailbox_verification import MailboxVerificationProof
    from app.models.nodes import User
    from app.services.member_provision import (
        _find_auth_user_by_email,
        provision_workspace_member,
    )
    from tests.test_member_provision_authority import setup_accounts

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "ab" * 32)
    caller, owner, _recipient, workspace, _email = await setup_accounts(
        client, authenticated_client, monkeypatch, verified=True, admin=False
    )
    work_email = "atomic.hire@example.com"
    async with caller:
        started = await svc.start_mailbox_verification(
            subject_entry_id="n.Entry.atomic-hire",
            workspace_id=str(workspace.id),
            email="personal.atomic@example.com",
            purpose="account_creation",
        )
        ok, _, proof_id = await svc.confirm_mailbox_verification(
            subject_entry_id="n.Entry.atomic-hire",
            workspace_id=str(workspace.id),
            purpose="account_creation",
            code=started["code"],
            proof_id=started["proof_id"],
        )
        assert ok is True
        with patch(
            "app.services.mailbox_verification.consume_mailbox_verification_proof",
            new=AsyncMock(return_value=(False, svc.ERR_PROOF_INVALID)),
        ):
            with pytest.raises(BadRequestError):
                await provision_workspace_member(
                    workspace=workspace,
                    actor_id=owner.id,
                    email=work_email,
                    display_name="Atomic Hire",
                    send_credentials=True,
                    personal_email="personal.atomic@example.com",
                    mailbox_verification_proof_id=proof_id,
                    mailbox_subject_entry_id="n.Entry.atomic-hire",
                    mailbox_verification_purpose="account_creation",
                )

    proof = await MailboxVerificationProof.get(proof_id)
    assert proof is not None and not proof.consumed_at
    assert await _find_auth_user_by_email(work_email) is None
    assert (
        await User.find({"context.personal_email": "personal.atomic@example.com"}) == []
    )
