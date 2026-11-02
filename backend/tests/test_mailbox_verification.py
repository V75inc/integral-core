"""Core mailbox verification and purpose-bound proof consumption."""

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.models.mailbox_verification import MailboxVerificationProof
from app.services import mailbox_verification as svc

PURPOSE = "public_submission"


@pytest.mark.asyncio
async def test_confirm_produces_proof_bound_to_email():
    started = await svc.start_mailbox_verification(
        subject_entry_id="n.Entry.c1",
        workspace_id="n.Workspace.ws1",
        email="Applicant@Example.com",
        purpose=PURPOSE,
    )
    assert started["ok"] is True
    code = started["code"]
    ok, err, proof_id = await svc.confirm_mailbox_verification(
        subject_entry_id="n.Entry.c1",
        workspace_id="n.Workspace.ws1",
        purpose=PURPOSE,
        code=code,
        proof_id=started["proof_id"],
    )
    assert ok is True and err is None and proof_id
    record = await MailboxVerificationProof.get(proof_id)
    assert record is not None
    assert record.verified_at
    assert record.email_norm == "applicant@example.com"
    assert not record.otp_hash


@pytest.mark.asyncio
async def test_wrong_code_and_reuse_blocked():
    started = await svc.start_mailbox_verification(
        subject_entry_id="n.Entry.c2",
        workspace_id="n.Workspace.ws1",
        email="a@example.com",
        purpose=PURPOSE,
    )
    ok, err, _ = await svc.confirm_mailbox_verification(
        subject_entry_id="n.Entry.c2",
        workspace_id="n.Workspace.ws1",
        purpose=PURPOSE,
        code="000000",
        proof_id=started["proof_id"],
    )
    assert ok is False and err == svc.ERR_INVALID_CODE
    ok2, _, proof_id = await svc.confirm_mailbox_verification(
        subject_entry_id="n.Entry.c2",
        workspace_id="n.Workspace.ws1",
        purpose=PURPOSE,
        code=started["code"],
        proof_id=started["proof_id"],
    )
    assert ok2 is True and proof_id
    consumed, cerr = await svc.consume_mailbox_verification_proof(
        proof_id=proof_id,
        subject_entry_id="n.Entry.c2",
        workspace_id="n.Workspace.ws1",
        personal_email="a@example.com",
        purpose=PURPOSE,
        consumed_for_user_id="n.User.u1",
    )
    assert consumed is True and cerr is None
    again, _ = await svc.consume_mailbox_verification_proof(
        proof_id=proof_id,
        subject_entry_id="n.Entry.c2",
        workspace_id="n.Workspace.ws1",
        personal_email="a@example.com",
        purpose=PURPOSE,
        consumed_for_user_id="n.User.u2",
    )
    assert again is False


@pytest.mark.asyncio
async def test_email_change_invalidates_via_restart():
    started = await svc.start_mailbox_verification(
        subject_entry_id="n.Entry.c3",
        workspace_id="n.Workspace.ws1",
        email="old@example.com",
        purpose=PURPOSE,
    )
    ok, _, proof_id = await svc.confirm_mailbox_verification(
        subject_entry_id="n.Entry.c3",
        workspace_id="n.Workspace.ws1",
        purpose=PURPOSE,
        code=started["code"],
        proof_id=started["proof_id"],
    )
    assert ok is True
    await svc.start_mailbox_verification(
        subject_entry_id="n.Entry.c3",
        workspace_id="n.Workspace.ws1",
        email="new@example.com",
        purpose=PURPOSE,
    )
    record = await MailboxVerificationProof.get(proof_id)
    assert record is not None
    assert record.invalidated_at


@pytest.mark.asyncio
async def test_manual_timestamp_cannot_consume_without_core_proof():
    """Client state cannot substitute for a Core-issued proof."""
    consumed, err = await svc.consume_mailbox_verification_proof(
        proof_id="o.ApplicationMailboxVerification.missing",
        subject_entry_id="n.Entry.staff",
        workspace_id="n.Workspace.ws1",
        personal_email="staff-set@example.com",
        purpose=PURPOSE,
        consumed_for_user_id="n.User.u1",
    )
    assert consumed is False and err == svc.ERR_PROOF_INVALID


@pytest.mark.asyncio
async def test_proof_cannot_be_confirmed_or_consumed_for_another_purpose():
    started = await svc.start_mailbox_verification(
        subject_entry_id="n.Entry.purpose-bound",
        workspace_id="n.Workspace.ws1",
        email="a@example.com",
        purpose=PURPOSE,
    )
    ok, err, _ = await svc.confirm_mailbox_verification(
        subject_entry_id="n.Entry.purpose-bound",
        workspace_id="n.Workspace.ws1",
        purpose="different_flow",
        code=started["code"],
        proof_id=started["proof_id"],
    )
    assert ok is False and err == svc.ERR_NOT_PENDING
    ok, _, proof_id = await svc.confirm_mailbox_verification(
        subject_entry_id="n.Entry.purpose-bound",
        workspace_id="n.Workspace.ws1",
        purpose=PURPOSE,
        code=started["code"],
        proof_id=started["proof_id"],
    )
    assert ok and proof_id
    ready = await svc.proof_ready_for_account_provisioning(
        proof_id=proof_id,
        subject_entry_id="n.Entry.purpose-bound",
        workspace_id="n.Workspace.ws1",
        personal_email="a@example.com",
        purpose="different_flow",
    )
    assert ready is False
