"""Tests for Plan 03 — Phase 5 (org storage accounting + quota + zip).

Backend coverage:
    - StorageUsage snapshot semantics (percent, soft-warn, would_exceed).
    - increment / decrement helpers no-op without an org.
    - increment / decrement update the counter when an org exists.
    - recalculate sums attachment bytes from the graph.
    - check_quota_for_entry honours the enforcement flag.
    - bulk-download zip endpoint (skip when in-process multipart bug
      blocks setup; cover the helper directly).
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.config import settings
from app.models.edges import HAS_ATTACHMENT
from app.models.nodes import Attachment, Entry, Track, Workspace
from app.services.workspace_storage_usage import (
    SOFT_WARN_THRESHOLD,
    StorageUsage,
    check_quota_for_entry,
    decrement_for_entry,
    get_usage,
    increment_for_entry,
    recalculate_for_workspace,
)

# ---------------------------------------------------------------------------
# Snapshot semantics (pure)
# ---------------------------------------------------------------------------


def test_usage_unlimited_when_quota_zero():
    u = StorageUsage(
        workspace_id="o",
        bytes_used=12345,
        quota_bytes=0,
        enforcement_enabled=True,
    )
    assert u.is_unlimited
    assert u.percent == 0.0
    assert not u.is_soft_warning
    assert not u.would_exceed(10**9)


def test_usage_soft_warning_threshold():
    quota = 1000
    threshold = int(quota * SOFT_WARN_THRESHOLD)
    just_under = StorageUsage(
        workspace_id="o",
        bytes_used=threshold - 1,
        quota_bytes=quota,
        enforcement_enabled=False,
    )
    at = StorageUsage(
        workspace_id="o",
        bytes_used=threshold,
        quota_bytes=quota,
        enforcement_enabled=False,
    )
    assert not just_under.is_soft_warning
    assert at.is_soft_warning


def test_usage_percent_caps_at_100():
    u = StorageUsage(
        workspace_id="o",
        bytes_used=2_000,
        quota_bytes=1_000,
        enforcement_enabled=True,
    )
    assert u.percent == 100.0
    assert u.would_exceed(1)


def test_usage_to_dict_shape():
    u = StorageUsage(
        workspace_id="o",
        bytes_used=10,
        quota_bytes=100,
        enforcement_enabled=True,
    )
    d = u.to_dict()
    # Keep the contract explicit — the frontend types depend on these names.
    assert d["workspace_id"] == "o"
    assert d["bytes_used"] == 10
    assert d["quota_bytes"] == 100
    assert d["percent"] == 10.0
    assert d["is_unlimited"] is False
    assert d["enforcement_enabled"] is True
    assert d["soft_warn_threshold"] == SOFT_WARN_THRESHOLD


# ---------------------------------------------------------------------------
# Increment / decrement + quota check round-trip
# ---------------------------------------------------------------------------


async def _make_org_track_entry(quota: int = 0):
    org = await Workspace.create(
        kind="organization",
        name="Acme",
        owner_user_id="u1",
        storage_bytes_used=0,
        storage_quota_bytes=quota,
        created_at=datetime.now().isoformat(),
    )
    track = await Track.create(
        title="T",
        title_fold="t",
        workspace_id=org.id,
        created_at=datetime.now().isoformat(),
    )
    entry = await Entry.create(
        title="e",
        track_id=track.id,
        attachment_ids=[],
        created_at=datetime.now().isoformat(),
    )
    return org, track, entry


@pytest.mark.asyncio
async def test_increment_and_decrement_round_trip():
    org, _, entry = await _make_org_track_entry()
    await increment_for_entry(entry, 1000)
    snap = await get_usage(org.id)
    assert snap is not None
    assert snap.bytes_used == 1000

    await decrement_for_entry(entry, 400)
    snap = await get_usage(org.id)
    assert snap is not None
    assert snap.bytes_used == 600


@pytest.mark.asyncio
async def test_decrement_floors_at_zero():
    org, _, entry = await _make_org_track_entry()
    await increment_for_entry(entry, 100)
    await decrement_for_entry(entry, 999)
    snap = await get_usage(org.id)
    assert snap is not None
    assert snap.bytes_used == 0


@pytest.mark.asyncio
async def test_no_op_when_no_organization():
    """A track without an workspace_id should leave no trail."""
    track = await Track.create(
        title="t", title_fold="t", created_at=datetime.now().isoformat()
    )
    entry = await Entry.create(
        title="e",
        track_id=track.id,
        attachment_ids=[],
        created_at=datetime.now().isoformat(),
    )
    # Both calls should silently no-op.
    await increment_for_entry(entry, 12345)
    await decrement_for_entry(entry, 12345)


@pytest.mark.asyncio
async def test_check_quota_only_when_enforcement_enabled(monkeypatch):
    org, _, entry = await _make_org_track_entry(quota=1000)
    await increment_for_entry(entry, 900)

    monkeypatch.setattr(settings, "STORAGE_QUOTA_ENFORCE", False)
    assert await check_quota_for_entry(entry, 500) is None  # over but disabled

    monkeypatch.setattr(settings, "STORAGE_QUOTA_ENFORCE", True)
    # Within remaining budget — no block.
    assert await check_quota_for_entry(entry, 100) is None
    # Over remaining budget — returns snapshot for the error message.
    snap = await check_quota_for_entry(entry, 500)
    assert snap is not None
    assert snap.bytes_used == 900
    assert snap.quota_bytes == 1000


# ---------------------------------------------------------------------------
# Recalculate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recalculate_sums_attachments_from_graph():
    org, track, entry = await _make_org_track_entry()
    # Drift the denormalised counter so recalc has work to do.
    org.storage_bytes_used = 999_999
    await org.save()

    # Stage attachments wired to the entry via HAS_ATTACHMENT.
    sizes = [100, 200, 300]
    for i, size in enumerate(sizes):
        att = await Attachment.create(
            filename=f"{i}.bin",
            mime_type="application/octet-stream",
            size=size,
            storage_key=f"k{i}",
            source_type="file",
            uploaded_by="u",
            created_at=datetime.now().isoformat(),
        )
        await entry.connect(
            att,
            edge=HAS_ATTACHMENT,
            attached_at=datetime.now().isoformat(),
            attached_by="u",
        )

    # CONTAINS edge: track → entry. recalculate walks via Track.nodes
    # so we need to ensure the link exists. The Entry's track_id alone
    # isn't enough for the graph walk used inside the service.
    from app.models.edges import CONTAINS

    await track.connect(
        entry,
        edge=CONTAINS,
        added_at=datetime.now().isoformat(),
    )

    snap = await recalculate_for_workspace(org.id)
    assert snap.bytes_used == sum(sizes)


@pytest.mark.asyncio
async def test_recalculate_includes_chat_files_once_and_respects_workspace():
    """Count unfiled chat blobs while avoiding provenance double counting."""
    from app.models.edges import CONTAINS
    from app.services import chat_threads

    workspace, track, entry = await _make_org_track_entry()
    other, _, _ = await _make_org_track_entry()
    await track.connect(entry, edge=CONTAINS, added_at=datetime.now().isoformat())
    thread = await chat_threads.create_thread(
        user_id="u1", provider_id="integral_native", workspace_id=workspace.id
    )
    other_thread = await chat_threads.create_thread(
        user_id="u1", provider_id="integral_native", workspace_id=other.id
    )

    async def attach(parent, size, owner, storage_key):
        attachment = await Attachment.create(
            filename="file.txt",
            size=size,
            storage_key=storage_key,
            owner_kind=owner,
            scan_status="skipped",
            uploaded_by="u1",
        )
        await parent.connect(attachment, edge=HAS_ATTACHMENT, attached_by="u1")
        return attachment

    await attach(thread, 100, "chat", "chat-key")
    await attach(thread, 500, "chat", "")  # no persisted blob
    filed = await attach(thread, 200, "entry", "filed-key")
    await entry.connect(filed, edge=HAS_ATTACHMENT, attached_by="u1")
    await attach(other_thread, 1000, "chat", "other-key")

    snap = await recalculate_for_workspace(workspace.id)
    assert snap.bytes_used == 300


@pytest.mark.asyncio
async def test_recalculate_does_not_persist_partial_total_when_chat_read_fails(
    monkeypatch,
):
    """A failed chat lookup must leave the existing usage counter intact."""
    from app.models.nodes import ChatThread

    workspace, _, _ = await _make_org_track_entry()
    workspace.storage_bytes_used = 1234
    await workspace.save()

    async def failed_find(*args: object, **kwargs: object):
        raise RuntimeError("chat storage unavailable")

    monkeypatch.setattr(ChatThread, "find", failed_find)
    with pytest.raises(RuntimeError, match="chat storage unavailable"):
        await recalculate_for_workspace(workspace.id)
    assert (await get_usage(workspace.id)).bytes_used == 1234


@pytest.mark.asyncio
async def test_recalculate_does_not_persist_partial_total_when_track_read_fails(
    monkeypatch,
):
    """Entry-side lookup failures must not reset the counter either."""
    workspace, _, _ = await _make_org_track_entry()
    workspace.storage_bytes_used = 1234
    await workspace.save()

    async def failed_find(*args: object, **kwargs: object):
        raise RuntimeError("track storage unavailable")

    monkeypatch.setattr(Track, "find", failed_find)
    with pytest.raises(RuntimeError, match="track storage unavailable"):
        await recalculate_for_workspace(workspace.id)
    assert (await get_usage(workspace.id)).bytes_used == 1234


@pytest.mark.asyncio
async def test_recalculate_refuses_to_overwrite_a_concurrent_usage_change(monkeypatch):
    """A successful delta during the scan must not be replaced by a stale sum."""
    from app.api.errors import ResourceConflictError
    from app.models.nodes import ChatThread
    from app.services.workspace_storage_usage import increment

    workspace, _, _ = await _make_org_track_entry()
    original_find = ChatThread.find

    async def increment_during_scan(*args: object, **kwargs: object):
        await increment(workspace.id, 75)
        return await original_find(*args, **kwargs)

    monkeypatch.setattr(ChatThread, "find", increment_during_scan)
    with pytest.raises(ResourceConflictError, match="Storage changed"):
        await recalculate_for_workspace(workspace.id)
    assert (await get_usage(workspace.id)).bytes_used == 75
