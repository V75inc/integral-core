"""Core application mailbox OTP and hire proof consumption."""

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.models.application_mailbox_verification import ApplicationMailboxVerification
from app.services import application_mailbox_verification as svc


@pytest.mark.asyncio
async def test_confirm_produces_proof_bound_to_email():
    started = await svc.start_application_mailbox_verification(
        candidate_entry_id="n.Entry.c1",
        workspace_id="n.Workspace.ws1",
        email="Applicant@Example.com",
    )
    assert started["ok"] is True
    code = started["code"]
    ok, err, proof_id = await svc.confirm_application_mailbox_verification(
        candidate_entry_id="n.Entry.c1",
        code=code,
        proof_id=started["proof_id"],
    )
    assert ok is True and err is None and proof_id
    record = await ApplicationMailboxVerification.get(proof_id)
    assert record is not None
    assert record.verified_at
    assert record.email_norm == "applicant@example.com"
    assert not record.otp_hash


@pytest.mark.asyncio
async def test_wrong_code_and_reuse_blocked():
    started = await svc.start_application_mailbox_verification(
        candidate_entry_id="n.Entry.c2",
        workspace_id="n.Workspace.ws1",
        email="a@example.com",
    )
    ok, err, _ = await svc.confirm_application_mailbox_verification(
        candidate_entry_id="n.Entry.c2",
        code="000000",
        proof_id=started["proof_id"],
    )
    assert ok is False and err == svc.ERR_INVALID_CODE
    ok2, _, proof_id = await svc.confirm_application_mailbox_verification(
        candidate_entry_id="n.Entry.c2",
        code=started["code"],
        proof_id=started["proof_id"],
    )
    assert ok2 is True and proof_id
    consumed, cerr = await svc.consume_application_mailbox_proof(
        proof_id=proof_id,
        candidate_entry_id="n.Entry.c2",
        workspace_id="n.Workspace.ws1",
        personal_email="a@example.com",
        consumed_for_user_id="n.User.u1",
    )
    assert consumed is True and cerr is None
    again, _ = await svc.consume_application_mailbox_proof(
        proof_id=proof_id,
        candidate_entry_id="n.Entry.c2",
        workspace_id="n.Workspace.ws1",
        personal_email="a@example.com",
        consumed_for_user_id="n.User.u2",
    )
    assert again is False


@pytest.mark.asyncio
async def test_email_change_invalidates_via_restart():
    started = await svc.start_application_mailbox_verification(
        candidate_entry_id="n.Entry.c3",
        workspace_id="n.Workspace.ws1",
        email="old@example.com",
    )
    ok, _, proof_id = await svc.confirm_application_mailbox_verification(
        candidate_entry_id="n.Entry.c3",
        code=started["code"],
        proof_id=started["proof_id"],
    )
    assert ok is True
    await svc.start_application_mailbox_verification(
        candidate_entry_id="n.Entry.c3",
        workspace_id="n.Workspace.ws1",
        email="new@example.com",
    )
    record = await ApplicationMailboxVerification.get(proof_id)
    assert record is not None
    assert record.invalidated_at


@pytest.mark.asyncio
async def test_manual_timestamp_cannot_consume_without_core_proof():
    """Recruitment readiness timestamps are not hire authorization."""
    consumed, err = await svc.consume_application_mailbox_proof(
        proof_id="o.ApplicationMailboxVerification.missing",
        candidate_entry_id="n.Entry.staff",
        workspace_id="n.Workspace.ws1",
        personal_email="staff-set@example.com",
        consumed_for_user_id="n.User.u1",
    )
    assert consumed is False and err == svc.ERR_PROOF_INVALID
