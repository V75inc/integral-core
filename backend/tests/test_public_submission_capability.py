"""Core reusable capabilities isolate anonymous mutations to one submission."""

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
