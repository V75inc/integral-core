"""Chat-thread file upload (Slice B, Task B2).

Targets ``_persist_uploaded_chat_file`` directly, mirroring the existing
convention for the entry-attachment pipeline (see test_attachment_pipeline.py)
— the HTTP-level multipart test is skipped project-wide because jvspatial's
``@endpoint`` decorator expects a JSON body, not multipart form data.
"""

from __future__ import annotations

import io

import pytest
from starlette.datastructures import Headers
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.api.attachments import _persist_uploaded_chat_file
from app.models.edges import HAS_ATTACHMENT
from app.models.nodes import Attachment
from app.services import chat_threads as chat_store


def _make_upload(
    content: bytes, filename: str, content_type: str
) -> StarletteUploadFile:
    return StarletteUploadFile(
        file=io.BytesIO(content),
        filename=filename,
        headers=Headers({"content-type": content_type}),
    )


async def _make_thread():
    return await chat_store.create_thread(
        user_id="u1",
        provider_id="jvagent",
        workspace_id="ws1",
    )


@pytest.mark.asyncio
async def test_chat_upload_persists_attachment_owned_by_thread():
    thread = await _make_thread()
    upload = _make_upload(b"hello world", "notes.txt", "text/plain")

    result = await _persist_uploaded_chat_file(thread=thread, user_id="u1", file=upload)

    attachment_export = result["attachment"]
    attachment = await Attachment.get(attachment_export["id"])
    assert attachment.owner_kind == "chat"
    assert attachment.filename == "notes.txt"
    assert attachment.uploaded_by == "u1"

    owners = await thread.nodes(
        edge=[HAS_ATTACHMENT], node=["Attachment"], direction="out"
    )
    assert any(a.id == attachment.id for a in owners)


@pytest.mark.asyncio
async def test_chat_upload_rejects_disallowed_mime():
    thread = await _make_thread()
    upload = _make_upload(b"MZ\x90\x00", "malware.exe", "application/x-msdownload")

    with pytest.raises(Exception):
        await _persist_uploaded_chat_file(thread=thread, user_id="u1", file=upload)


@pytest.mark.asyncio
async def test_chat_upload_dedups_identical_content_on_same_thread():
    """Re-upload of the same bytes returns the existing attachment (idempotent)."""
    thread = await _make_thread()
    upload1 = _make_upload(b"same bytes", "a.txt", "text/plain")
    upload2 = _make_upload(b"same bytes", "b.txt", "text/plain")

    first = await _persist_uploaded_chat_file(
        thread=thread, user_id="u1", file=upload1
    )
    second = await _persist_uploaded_chat_file(
        thread=thread, user_id="u1", file=upload2
    )
    assert first["attachment"]["id"] == second["attachment"]["id"]
    assert second.get("deduplicated") is True


@pytest.mark.asyncio
async def test_chat_upload_same_content_allowed_on_different_thread():
    """Dedup is scoped to a single thread, not global."""
    thread_a = await _make_thread()
    thread_b = await _make_thread()
    upload_a = _make_upload(b"shared bytes", "a.txt", "text/plain")
    upload_b = _make_upload(b"shared bytes", "b.txt", "text/plain")

    await _persist_uploaded_chat_file(thread=thread_a, user_id="u1", file=upload_a)
    result_b = await _persist_uploaded_chat_file(
        thread=thread_b, user_id="u1", file=upload_b
    )
    assert result_b["attachment"]["filename"] == "b.txt"
