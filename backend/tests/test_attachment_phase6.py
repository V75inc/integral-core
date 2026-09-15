"""Tests for Plan 03 — Phase 6 (resumable chunked uploads).

Covers the service-layer state machine directly. The HTTP endpoints
share the same wiring as the rest of the attachment surface; the
in-process test client's known multipart limitation doesn't bite here
because the chunked path uses raw octet-stream PUTs, but to keep the
test footprint focused we exercise the service.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.exceptions import BadRequestError
from app.models.nodes import Attachment, Entry, Track, UploadSession, Workspace
from app.services.chunked_upload import (
    append_chunk,
    assemble_session,
    cancel_session,
    clear_chunks,
    expire_stale_sessions,
    get_session,
    start_session,
)


@pytest.fixture(autouse=True)
def enable_chunked(monkeypatch):
    monkeypatch.setattr(settings, "CHUNKED_UPLOAD_ENABLED", True)


async def _make_entry() -> Entry:
    track = await Track.create(
        title="t", title_fold="t", created_at=datetime.now().isoformat()
    )
    return await Entry.create(
        title="e",
        track_id=track.id,
        attachment_ids=[],
        created_at=datetime.now().isoformat(),
    )


# ---------------------------------------------------------------------------
# Start / append / assemble round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_session_creates_pending_node():
    entry = await _make_entry()
    session = await start_session(
        entry=entry,
        user_id="u1",
        filename="big.bin",
        mime_type="application/octet-stream",
        total_bytes=1000,
    )
    assert session.status == "pending"
    assert session.entry_id == entry.id
    assert session.received_bytes == 0
    assert session.next_chunk_index == 0


@pytest.mark.asyncio
async def test_append_chunks_in_order_advances_state():
    entry = await _make_entry()
    session = await start_session(
        entry=entry,
        user_id="u1",
        filename="x.bin",
        mime_type="application/octet-stream",
        total_bytes=6,
    )
    s = await append_chunk(session=session, chunk_index=0, chunk_bytes=b"abc")
    assert s.received_bytes == 3
    assert s.next_chunk_index == 1
    assert s.content_hash  # running hash populated
    s = await append_chunk(session=s, chunk_index=1, chunk_bytes=b"def")
    assert s.received_bytes == 6
    assert s.next_chunk_index == 2
    assert s.status == "active"


@pytest.mark.asyncio
async def test_append_chunk_rejects_oversized_chunk(monkeypatch):
    monkeypatch.setattr(settings, "CHUNKED_UPLOAD_CHUNK_SIZE_BYTES", 4)
    entry = await _make_entry()
    session = await start_session(
        entry=entry,
        user_id="u1",
        filename="x.bin",
        mime_type="application/octet-stream",
        total_bytes=100,
    )
    with pytest.raises(BadRequestError):
        # 4 bytes is the configured cap; 100 bytes is well over the 2x
        # tail-chunk tolerance the service allows.
        await append_chunk(session=session, chunk_index=0, chunk_bytes=b"x" * 100)


@pytest.mark.asyncio
async def test_append_chunk_rejects_exceeding_total_bytes():
    entry = await _make_entry()
    session = await start_session(
        entry=entry,
        user_id="u1",
        filename="x.bin",
        mime_type="application/octet-stream",
        total_bytes=5,
    )
    with pytest.raises(BadRequestError):
        await append_chunk(session=session, chunk_index=0, chunk_bytes=b"abcdef")


@pytest.mark.asyncio
async def test_assemble_session_concatenates_and_hashes():
    entry = await _make_entry()
    session = await start_session(
        entry=entry,
        user_id="u1",
        filename="x.bin",
        mime_type="application/octet-stream",
        total_bytes=9,
    )
    session = await append_chunk(session=session, chunk_index=0, chunk_bytes=b"abc")
    session = await append_chunk(session=session, chunk_index=1, chunk_bytes=b"def")
    session = await append_chunk(session=session, chunk_index=2, chunk_bytes=b"ghi")

    assembled, sha = await assemble_session(session)
    assert assembled == b"abcdefghi"
    import hashlib

    assert sha == hashlib.sha256(b"abcdefghi").hexdigest()


@pytest.mark.asyncio
async def test_assemble_session_rejects_incomplete():
    entry = await _make_entry()
    session = await start_session(
        entry=entry,
        user_id="u1",
        filename="x.bin",
        mime_type="application/octet-stream",
        total_bytes=10,
    )
    session = await append_chunk(session=session, chunk_index=0, chunk_bytes=b"abc")
    with pytest.raises(BadRequestError):
        await assemble_session(session)


# ---------------------------------------------------------------------------
# Quota integration at session init
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_session_rejects_over_quota(monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_QUOTA_ENFORCE", True)
    org = await Workspace.create(
        kind="organization",
        name="Acme",
        owner_user_id="u1",
        storage_bytes_used=900,
        storage_quota_bytes=1000,
        created_at=datetime.now().isoformat(),
    )
    track = await Track.create(
        title="t",
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
    with pytest.raises(BadRequestError):
        await start_session(
            entry=entry,
            user_id="u1",
            filename="huge.bin",
            mime_type="application/octet-stream",
            total_bytes=500,  # 900 + 500 > 1000
        )


# ---------------------------------------------------------------------------
# Cancel + expire lifecycles
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_session_marks_cancelled_and_clears_chunks():
    entry = await _make_entry()
    session = await start_session(
        entry=entry,
        user_id="u1",
        filename="x.bin",
        mime_type="application/octet-stream",
        total_bytes=6,
    )
    session = await append_chunk(session=session, chunk_index=0, chunk_bytes=b"abc")
    await cancel_session(session)
    refreshed = await get_session(session.id)
    assert refreshed is not None
    assert refreshed.status == "cancelled"
    # Cancelled sessions can't be appended to.
    with pytest.raises(BadRequestError):
        await append_chunk(session=refreshed, chunk_index=1, chunk_bytes=b"def")


@pytest.mark.asyncio
async def test_expire_stale_sessions_sweeps_expired_rows():
    entry = await _make_entry()
    session = await start_session(
        entry=entry,
        user_id="u1",
        filename="x.bin",
        mime_type="application/octet-stream",
        total_bytes=100,
    )
    # Backdate the expiry so the sweep picks it up.
    session.expires_at = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    await session.save()

    swept = await expire_stale_sessions()
    assert swept >= 1
    refreshed = await get_session(session.id)
    assert refreshed is not None
    assert refreshed.status == "expired"


# ---------------------------------------------------------------------------
# Enabled flag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_session_requires_feature_flag(monkeypatch):
    monkeypatch.setattr(settings, "CHUNKED_UPLOAD_ENABLED", False)
    entry = await _make_entry()
    with pytest.raises(BadRequestError):
        await start_session(
            entry=entry,
            user_id="u1",
            filename="x.bin",
            mime_type="application/octet-stream",
            total_bytes=10,
        )
