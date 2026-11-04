"""Core mailbox verification and purpose-bound proof consumption."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.models.mailbox_verification import MailboxVerificationProof
from app.services import mailbox_verification as svc

PURPOSE = "public_submission"


@pytest.mark.asyncio
async def test_confirmation_without_proof_id_selects_latest_beyond_old_history():
    started = await svc.start_mailbox_verification(
        subject_entry_id="n.Entry.latest-proof",
        workspace_id="n.Workspace.ws1",
        email="a@example.com",
        purpose=PURPOSE,
    )
    for index in range(21):
        await MailboxVerificationProof.create(
            subject_entry_id="n.Entry.latest-proof",
            workspace_id="n.Workspace.ws1",
            email_norm="a@example.com",
            purpose=PURPOSE,
            created_at=f"2000-01-{index + 1:02d}T00:00:00+00:00",
            invalidated_at="2000-02-01T00:00:00+00:00",
        )
    ok, error, proof_id = await svc.confirm_mailbox_verification(
        subject_entry_id="n.Entry.latest-proof",
        workspace_id="n.Workspace.ws1",
        purpose=PURPOSE,
        code=started["code"],
    )
    assert ok and error is None and proof_id == started["proof_id"]


@pytest.mark.asyncio
async def test_legacy_subject_is_migrated_and_attempt_limit_is_preserved(monkeypatch):
    monkeypatch.setattr(svc.settings, "EMAIL_VERIFICATION_MAX_ATTEMPTS", 2)
    started = await svc.start_mailbox_verification(
        subject_entry_id="n.Entry.legacy-proof",
        workspace_id="n.Workspace.ws1",
        email="a@example.com",
        purpose=PURPOSE,
    )
    record = await MailboxVerificationProof.get(started["proof_id"])
    record.subject_entry_id = ""
    record.candidate_entry_id = "n.Entry.legacy-proof"
    await record.save()
    args = {
        "subject_entry_id": "n.Entry.legacy-proof",
        "workspace_id": "n.Workspace.ws1",
        "purpose": PURPOSE,
        "proof_id": record.id,
    }
    wrong_code = str((int(started["code"]) + 1) % 1_000_000).zfill(6)
    assert (await svc.confirm_mailbox_verification(**args, code=wrong_code))[
        1
    ] == svc.ERR_INVALID_CODE
    assert (await svc.confirm_mailbox_verification(**args, code=wrong_code))[
        1
    ] == svc.ERR_ATTEMPTS_EXCEEDED
    assert (await svc.confirm_mailbox_verification(**args, code=started["code"]))[
        0
    ] is False
    from jvspatial.core.context import get_default_context

    rows = await get_default_context().find(MailboxVerificationProof, {"id": record.id})
    assert rows[0].otp_attempts == 2
    assert rows[0].subject_entry_id == "n.Entry.legacy-proof"
    assert rows[0].candidate_entry_id is None


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_concurrent_workers_can_consume_a_proof_only_once(monkeypatch):
    started = await svc.start_mailbox_verification(
        subject_entry_id="n.Entry.concurrent-proof",
        workspace_id="n.Workspace.ws1",
        email="a@example.com",
        purpose=PURPOSE,
    )
    ok, _, proof_id = await svc.confirm_mailbox_verification(
        subject_entry_id="n.Entry.concurrent-proof",
        workspace_id="n.Workspace.ws1",
        purpose=PURPOSE,
        code=started["code"],
        proof_id=started["proof_id"],
    )
    assert ok
    original_get = MailboxVerificationProof.get
    verified = await original_get(proof_id)
    monkeypatch.setattr(svc, "utc_now_iso", lambda: verified.updated_at)
    barrier = asyncio.Barrier(2)

    async def independent_worker_read(identifier):
        row = await original_get(identifier)
        isolated = MailboxVerificationProof.model_validate(row.model_dump())
        await barrier.wait()
        return isolated

    monkeypatch.setattr(MailboxVerificationProof, "get", independent_worker_read)
    results = await asyncio.wait_for(
        asyncio.gather(
            *(
                svc.consume_mailbox_verification_proof(
                    proof_id=proof_id,
                    subject_entry_id="n.Entry.concurrent-proof",
                    workspace_id="n.Workspace.ws1",
                    personal_email="a@example.com",
                    purpose=PURPOSE,
                    consumed_for_user_id=f"n.User.worker-{worker}",
                )
                for worker in range(2)
            )
        ),
        timeout=15,
    )
    assert sum(ok for ok, _error in results) == 1


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
