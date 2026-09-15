"""Resumable chunked uploads (Plan 03 — Phase 6).

The single-shot multipart path (``POST /entries/{id}/attachments``)
caps out at ``ATTACHMENT_MAX_UPLOAD_BYTES`` because we accumulate the
bytes in memory before writing. Files larger than that — or transfers
that need to survive a flaky network — flow through this module:

    1. ``start_session`` reserves an UploadSession node with the
       declared total size + chunk size. Quota is checked here, *before*
       any bytes are spent — fail fast.
    2. ``append_chunk`` writes a single chunk to a server-local
       staging directory (``<tempdir>/integral-chunked-uploads/{session_id}/<n>.part``).
       The running SHA-256 + counters are updated alongside the part write.
    3. ``complete_session`` concatenates the staged parts into the
       final blob, then hands off to
       ``app.api.attachments._persist_assembled_content`` (the
       sniff / scan / metadata pipeline) which writes the final bytes
       through the standard jvspatial.storage facade.
    4. ``cancel_session`` and ``expire_sessions`` clean up partial
       state.

Design choices worth flagging:

- Chunk staging is deliberately *local-filesystem only* rather than
  going through jvspatial.storage. Chunks are transient server-local
  state — sending them through the user-content storage backend adds
  S3 round-trips for nothing, and trips jvspatial's content-type
  allow-list (the ``.part`` extension sniffs as
  ``application/octet-stream`` which the local backend rejects). The
  trade-off: multi-instance deployments can't resume a session on a
  different replica. We can revisit by promoting staging to S3
  multipart-upload (native chunk support, no allow-list issue) when
  needed.
- The running content hash is computed server-side. We *don't* trust
  a client-supplied hash because the dedup check relies on it.
- Out-of-order chunks are permitted but discouraged. The server
  records ``next_chunk_index`` for the client's convenience.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from app.config import settings
from app.exceptions import BadRequestError
from app.models.nodes import Entry, UploadSession
from app.services.workspace_storage_usage import check_quota_for_entry

logger = logging.getLogger(__name__)


# Local-filesystem prefix for staged chunks. Living under the system
# tempdir means OS-level eviction policies will eventually reclaim any
# orphan dirs we miss — and on most platforms ``/tmp`` is wiped on
# reboot, which is the right behaviour for in-flight uploads.
_STAGING_ROOT_NAME = "integral-chunked-uploads"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _expiry_iso() -> str:
    delta = timedelta(hours=settings.CHUNKED_UPLOAD_SESSION_TTL_HOURS)
    return (datetime.now(timezone.utc) + delta).isoformat()


def _ensure_enabled() -> None:
    if not settings.CHUNKED_UPLOAD_ENABLED:
        raise BadRequestError(
            message=(
                "Chunked / resumable uploads are not enabled on this "
                "deployment. Set CHUNKED_UPLOAD_ENABLED=true to opt in."
            )
        )


def _staging_root() -> str:
    """Server-local staging directory. Created lazily."""
    return os.path.join(tempfile.gettempdir(), _STAGING_ROOT_NAME)


def _session_dir(session_id: str) -> str:
    return os.path.join(_staging_root(), session_id)


def _chunk_path(session_id: str, chunk_index: int) -> str:
    return os.path.join(_session_dir(session_id), f"{int(chunk_index):08d}.part")


def _write_chunk_file(path: str, chunk_bytes: bytes) -> None:
    with open(path, "wb") as f:
        f.write(chunk_bytes)


def _read_chunk_file(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def _ensure_session_dir(session_id: str) -> None:
    os.makedirs(_session_dir(session_id), exist_ok=True)


async def start_session(
    *,
    entry: Entry,
    user_id: str,
    filename: str,
    mime_type: str,
    total_bytes: int,
    chunk_size: Optional[int] = None,
) -> UploadSession:
    """Reserve a new upload session.

    Raises:
        BadRequestError when chunked uploads are disabled, when the
        declared total is non-positive or exceeds the configured cap,
        or when the parent org's storage quota would be exceeded.
    """
    _ensure_enabled()
    if total_bytes <= 0:
        raise BadRequestError(message="total_bytes must be a positive integer")
    if total_bytes > settings.CHUNKED_UPLOAD_MAX_TOTAL_BYTES:
        raise BadRequestError(
            message=(
                f"Declared upload size {total_bytes} exceeds the maximum "
                f"({settings.CHUNKED_UPLOAD_MAX_TOTAL_BYTES} bytes)."
            )
        )

    over_quota = await check_quota_for_entry(entry, total_bytes)
    if over_quota is not None:
        raise BadRequestError(
            message=(
                "Workspace storage quota would be exceeded by this upload "
                f"({over_quota.bytes_used} / {over_quota.quota_bytes} bytes)."
            )
        )

    cs = int(chunk_size or settings.CHUNKED_UPLOAD_CHUNK_SIZE_BYTES)
    if cs <= 0:
        raise BadRequestError(message="chunk_size must be a positive integer")

    now_iso = _now_iso()
    session = await UploadSession.create(
        entry_id=entry.id,
        uploaded_by=user_id,
        filename=filename or "upload",
        mime_type=(mime_type or "").strip().lower() or "application/octet-stream",
        total_bytes=int(total_bytes),
        received_bytes=0,
        chunk_size=cs,
        next_chunk_index=0,
        content_hash="",
        status="pending",
        created_at=now_iso,
        updated_at=now_iso,
        expires_at=_expiry_iso(),
    )
    # Phase 10.5 Plan 10.5-03 (I-GRAPH-01): wire Entry -HAS_UPLOAD_SESSION-> UploadSession
    # so the transient session is reachable from the rooted Entry
    # subgraph. Best-effort try/except — failure leaves the scalar
    # entry_id intact and the audit walker surfaces any gap.
    from app.models.edges import HAS_UPLOAD_SESSION

    try:
        await entry.connect(session, edge=HAS_UPLOAD_SESSION, started_at=now_iso)
    except Exception as exc:
        logger.exception(
            "start_upload_session: HAS_UPLOAD_SESSION wire failed for "
            "entry=%s session=%s",
            entry.id,
            session.id,
        )
        try:
            await session.delete()
        except Exception:
            logger.exception(
                "start_upload_session: rollback delete failed for session=%s",
                session.id,
            )
        raise BadRequestError(
            message="Could not attach upload session to entry."
        ) from exc
    return session


async def get_session(session_id: str) -> Optional[UploadSession]:
    if not session_id:
        return None
    return await UploadSession.get(session_id)


def _assert_session_open(session: UploadSession) -> None:
    if session.status in ("complete", "cancelled", "expired"):
        raise BadRequestError(
            message=f"Upload session is {session.status}; start a new one."
        )
    if session.expires_at:
        try:
            exp = datetime.fromisoformat(session.expires_at)
        except ValueError:
            exp = None
        if exp and datetime.now(timezone.utc) > exp:
            raise BadRequestError(message="Upload session expired; start a new one.")


async def append_chunk(
    *,
    session: UploadSession,
    chunk_index: int,
    chunk_bytes: bytes,
) -> UploadSession:
    """Persist a single chunk and update the session.

    The running hash is only well-defined for in-order writes; we
    detect out-of-order chunks and skip the hash update, falling back
    to a finalize-time re-hash for those cases.
    """
    _ensure_enabled()
    _assert_session_open(session)

    if not chunk_bytes:
        raise BadRequestError(message="Chunk body is empty")
    chunk_size_limit = settings.CHUNKED_UPLOAD_CHUNK_SIZE_BYTES
    if len(chunk_bytes) > chunk_size_limit * 2:
        # Allow 2x for tail chunks but reject anything substantially over.
        raise BadRequestError(
            message=(
                f"Chunk size {len(chunk_bytes)} exceeds configured limit "
                f"({chunk_size_limit} bytes)."
            )
        )

    if session.received_bytes + len(chunk_bytes) > session.total_bytes:
        raise BadRequestError(
            message=(
                "Chunk would exceed declared total_bytes for this session "
                f"({session.received_bytes + len(chunk_bytes)} > {session.total_bytes})."
            )
        )

    # Server-local filesystem staging (see module docstring for why
    # we don't route this through jvspatial.storage).
    await asyncio.to_thread(_ensure_session_dir, session.id)
    path = _chunk_path(session.id, chunk_index)
    await asyncio.to_thread(_write_chunk_file, path, chunk_bytes)

    session.received_bytes += len(chunk_bytes)
    # Track the highest contiguous chunk index seen so far so clients
    # have a stable resume point. Out-of-order chunks don't advance it.
    if chunk_index == session.next_chunk_index:
        session.next_chunk_index = chunk_index + 1
        # Running hash is safe to update only when chunks land in order.
        if session.content_hash:
            hasher = hashlib.sha256(bytes.fromhex(session.content_hash))
        else:
            hasher = hashlib.sha256()
        hasher.update(chunk_bytes)
        session.content_hash = hasher.hexdigest()
    session.status = "active"
    session.updated_at = _now_iso()
    await session.save()
    return session


async def cancel_session(session: UploadSession) -> None:
    """Mark cancelled and clear any staged chunks."""
    await asyncio.to_thread(_remove_session_dir, session.id)
    session.status = "cancelled"
    session.updated_at = _now_iso()
    await session.save()


def _remove_session_dir(session_id: str) -> None:
    """Best-effort recursive removal of the session's staging dir."""
    path = _session_dir(session_id)
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass


async def assemble_session(session: UploadSession) -> Tuple[bytes, str]:
    """Concatenate the staged parts into a single byte buffer + hash.

    Returns ``(bytes, sha256_hex)``. Used by the completion endpoint
    to hand a finished blob back to the standard persist pipeline.

    Note: holds the whole assembled file in memory. The S3-multipart
    optimisation (server-side concat without download) is the right
    next step when needed.
    """
    _ensure_enabled()
    _assert_session_open(session)

    if session.received_bytes != session.total_bytes:
        raise BadRequestError(
            message=(
                f"Upload incomplete: received {session.received_bytes} / "
                f"{session.total_bytes} bytes."
            )
        )

    chunks: list[bytes] = []
    hasher = hashlib.sha256()
    for i in range(session.next_chunk_index):
        path = _chunk_path(session.id, i)
        if not await asyncio.to_thread(os.path.isfile, path):
            raise BadRequestError(
                message=(
                    f"Chunk {i} missing from staging; cannot finalize. "
                    "Restart the upload."
                )
            )
        blob = await asyncio.to_thread(_read_chunk_file, path)
        chunks.append(blob)
        hasher.update(blob)
    assembled = b"".join(chunks)
    if len(assembled) != session.total_bytes:
        raise BadRequestError(
            message=(
                f"Assembled size mismatch: got {len(assembled)} bytes, "
                f"declared {session.total_bytes}."
            )
        )
    return assembled, hasher.hexdigest()


async def clear_chunks(session: UploadSession) -> None:
    """Remove the staged chunks for a session (post-finalize cleanup)."""
    await asyncio.to_thread(_remove_session_dir, session.id)


async def expire_stale_sessions() -> int:
    """Sweep job entry point — marks expired sessions and clears chunks.

    Returns the number of sessions transitioned. Caller is responsible
    for scheduling; we don't run this on a timer here.
    """
    swept = 0
    now = datetime.now(timezone.utc)
    candidates = await UploadSession.find({"status": {"$in": ["pending", "active"]}})
    for session in candidates:
        if not session.expires_at:
            continue
        try:
            exp = datetime.fromisoformat(session.expires_at)
        except ValueError:
            continue
        if now > exp:
            await clear_chunks(session)
            session.status = "expired"
            session.updated_at = _now_iso()
            await session.save()
            swept += 1
    return swept


def session_to_dict(session: UploadSession) -> dict:
    """Stable wire-shape for the chunked-upload endpoints."""
    remaining = max(0, session.total_bytes - session.received_bytes)
    return {
        "id": session.id,
        "entry_id": session.entry_id,
        "uploaded_by": session.uploaded_by,
        "filename": session.filename,
        "mime_type": session.mime_type,
        "total_bytes": session.total_bytes,
        "received_bytes": session.received_bytes,
        "remaining_bytes": remaining,
        "chunk_size": session.chunk_size,
        "next_chunk_index": session.next_chunk_index,
        "content_hash": session.content_hash,
        "status": session.status,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
        "expires_at": session.expires_at,
        "error": session.error,
    }
