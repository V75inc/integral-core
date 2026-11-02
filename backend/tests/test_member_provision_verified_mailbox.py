"""Member provision with a purpose-bound mailbox verification proof."""

from unittest.mock import AsyncMock, patch

import pytest

from app.services import mailbox_verification as svc


@pytest.mark.asyncio
async def test_provision_accepts_valid_proof_and_calls_activation_delivery(
    client, authenticated_client, monkeypatch
):
    from tests.test_member_provision_authority import setup_accounts

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
        with patch(
            "app.services.credential_delivery.deliver_verified_mailbox_activation_link",
            new=AsyncMock(
                return_value={
                    "intent_id": "i1",
                    "activation_emailed": True,
                    "delivery_retryable": False,
                }
            ),
        ) as deliver:
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
        deliver.assert_awaited_once()
