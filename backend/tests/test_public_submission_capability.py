"""Core reusable capabilities isolate anonymous mutations to one submission."""

import asyncio
from datetime import timedelta

import pytest

from app.services.public_submission_capability import (
    bind_public_submission_capability,
    issue_public_submission_capability,
    revoke_public_submission_capability,
    validate_public_entry_capability,
    validate_public_submission_capability,
)

_SCOPE = {
    "workspace_id": "n.Workspace.w1",
    "track_id": "n.Track.t1",
    "form_id": "n.EntryType.candidate",
    "share_link_id": "n.ShareLink.l1",
    "submission_id": "pending.p1",
}


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_concurrent_workers_cannot_bind_one_capability_to_two_entries(
    monkeypatch,
):
    from app.models.public_submission_capability import PublicSubmissionCapability

    issued = await _issued()
    original_get = PublicSubmissionCapability.get
    barrier = asyncio.Barrier(2)

    async def independent_worker_read(identifier):
        row = await original_get(identifier)
        isolated = PublicSubmissionCapability.model_validate(row.model_dump())
        await barrier.wait()
        return isolated

    monkeypatch.setattr(PublicSubmissionCapability, "get", independent_worker_read)
    results = await asyncio.wait_for(
        asyncio.gather(
            *(
                bind_public_submission_capability(
                    issued["capability"],
                    **_SCOPE,
                    entry_id=f"n.Entry.worker-{worker}",
                )
                for worker in range(2)
            )
        ),
        timeout=15,
    )
    assert sum(results) == 1


async def _issued(**overrides):
    return await issue_public_submission_capability(**(_SCOPE | overrides))


@pytest.mark.asyncio
async def test_capability_binds_pending_submission_then_exact_entry():
    issued = await _issued()
    assert await validate_public_submission_capability(issued["capability"], **_SCOPE)
    assert await bind_public_submission_capability(
        issued["capability"],
        **_SCOPE,
        entry_id="n.Entry.e1",
    )
    assert await validate_public_submission_capability(
        issued["capability"],
        **_SCOPE,
        entry_id="n.Entry.e1",
    )
    assert await validate_public_entry_capability(
        issued["capability"],
        workspace_id=_SCOPE["workspace_id"],
        track_id=_SCOPE["track_id"],
        form_id=_SCOPE["form_id"],
        share_link_id=_SCOPE["share_link_id"],
        entry_id="n.Entry.e1",
    )
    assert not await validate_public_submission_capability(
        issued["capability"],
        **_SCOPE,
        entry_id="n.Entry.e2",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mismatch",
    [
        {"submission_id": "pending.other"},
        {"workspace_id": "n.Workspace.w2"},
        {"track_id": "n.Track.t2"},
        {"form_id": "n.EntryType.other"},
        {"share_link_id": "n.ShareLink.other"},
    ],
)
async def test_capability_rejects_wrong_submission_or_scope(mismatch):
    issued = await _issued()
    assert not await validate_public_submission_capability(
        issued["capability"], **(_SCOPE | mismatch)
    )


@pytest.mark.asyncio
async def test_missing_invalid_expired_and_revoked_capabilities_are_denied():
    issued = await _issued()
    assert not await validate_public_submission_capability("", **_SCOPE)
    assert not await validate_public_submission_capability("wrong-secret", **_SCOPE)

    expired = await _issued(ttl=timedelta(seconds=-1))
    assert not await validate_public_submission_capability(
        expired["capability"], **_SCOPE
    )

    assert await revoke_public_submission_capability(
        issued["capability_id"], submission_id=_SCOPE["submission_id"]
    )
    assert not await validate_public_submission_capability(
        issued["capability"], **_SCOPE
    )
