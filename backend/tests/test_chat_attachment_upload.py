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
        provider_id="integral_native",
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
async def test_chat_upload_service_rejects_a_different_thread_owner():
    """A trusted integration cannot upload under someone else's chat."""
    from app.api.errors import ResourceNotFoundError

    thread = await _make_thread()
    upload = _make_upload(b"private", "notes.txt", "text/plain")
    with pytest.raises(ResourceNotFoundError):
        await _persist_uploaded_chat_file(thread=thread, user_id="u2", file=upload)
    assert await thread.count_nodes(edge=[HAS_ATTACHMENT], direction="out") == 0


@pytest.mark.asyncio
async def test_chat_upload_cleans_graph_and_partial_blob_on_storage_failure(
    monkeypatch,
):
    """A failed storage write leaves neither its graph node nor partial blob."""
    from app.services import chat_attachment_upload

    thread = await _make_thread()
    created = []
    original_create = Attachment.create

    async def create(**kwargs):
        attachment = await original_create(**kwargs)
        created.append(attachment.id)
        return attachment

    class FailedStorage:
        deleted = []

        def build_storage_key(self, entry_id, attachment_id, filename):
            return f"{entry_id}/{attachment_id}/{filename}"

        async def save_attachment(self, **kwargs):
            raise RuntimeError("partial storage failure")

        async def delete_attachment(self, key):
            self.deleted.append(key)

    storage = FailedStorage()
    monkeypatch.setattr(Attachment, "create", create)
    monkeypatch.setattr(
        chat_attachment_upload, "get_attachment_storage_service", lambda: storage
    )
    with pytest.raises(RuntimeError, match="partial storage failure"):
        await _persist_uploaded_chat_file(
            thread=thread,
            user_id="u1",
            file=_make_upload(b"draft", "draft.txt", "text/plain"),
        )
    assert len(created) == 1
    assert await Attachment.get(created[0]) is None
    assert await thread.count_nodes(edge=[HAS_ATTACHMENT], direction="out") == 0
    assert storage.deleted == [f"chat/{thread.id}/{created[0]}/draft.txt"]


@pytest.mark.asyncio
async def test_chat_upload_rolls_back_record_if_parent_edge_fails(monkeypatch):
    """An attachment cannot survive a failed connection to its chat."""
    from app.models.nodes import ChatThread

    thread = await _make_thread()
    created = []
    original_create = Attachment.create

    async def create(**kwargs):
        attachment = await original_create(**kwargs)
        created.append(attachment.id)
        return attachment

    async def failed_connect(self, *args, **kwargs):
        raise RuntimeError("injected graph edge failure")

    monkeypatch.setattr(Attachment, "create", create)
    monkeypatch.setattr(ChatThread, "connect", failed_connect)
    with pytest.raises(RuntimeError, match="injected graph edge failure"):
        await _persist_uploaded_chat_file(
            thread=thread,
            user_id="u1",
            file=_make_upload(b"draft", "draft.txt", "text/plain"),
        )
    assert len(created) == 1
    assert await Attachment.get(created[0]) is None


@pytest.mark.asyncio
async def test_chat_upload_dedups_identical_content_on_same_thread():
    """Re-upload of the same bytes returns the existing attachment (idempotent)."""
    thread = await _make_thread()
    upload1 = _make_upload(b"same bytes", "a.txt", "text/plain")
    upload2 = _make_upload(b"same bytes", "b.txt", "text/plain")

    first = await _persist_uploaded_chat_file(thread=thread, user_id="u1", file=upload1)
    second = await _persist_uploaded_chat_file(
        thread=thread, user_id="u1", file=upload2
    )
    assert first["attachment"]["id"] == second["attachment"]["id"]
    assert second.get("deduplicated") is True


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_concurrent_chat_upload_waits_for_scan_before_reuse(monkeypatch):
    """A retry cannot create a duplicate or expose an in-flight attachment."""
    import asyncio

    from app.api.errors import ResourceConflictError
    from app.services import chat_attachment_upload
    from app.services.attachment_agent import validate_chat_attachment_source
    from tests.contract.test_staging_command_apply_postgres import proposal

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "22" * 32)
    auth, workspace, _, _ = await proposal()
    thread = await chat_store.create_thread(
        user_id=auth.id, provider_id="integral_native", workspace_id=workspace.id
    )
    entered = asyncio.Event()
    release = asyncio.Event()
    scanner = chat_attachment_upload.get_attachment_scanner()

    class PausedScanner:
        name = scanner.name

        async def scan(self, **kwargs):
            entered.set()
            await release.wait()
            return await scanner.scan(**kwargs)

    monkeypatch.setattr(
        chat_attachment_upload,
        "get_attachment_scanner",
        lambda: PausedScanner(),
    )

    async def upload():
        return await _persist_uploaded_chat_file(
            thread=thread,
            user_id=auth.id,
            file=_make_upload(b"concurrent content", "a.txt", "text/plain"),
        )

    first = asyncio.create_task(upload())
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
        with pytest.raises(ResourceConflictError, match="still being prepared"):
            await upload()
        assert (
            await thread.count_nodes(
                edge=[HAS_ATTACHMENT], node=["Attachment"], direction="out"
            )
            == 1
        )
        candidates = await thread.nodes(
            edge=[HAS_ATTACHMENT], node=["Attachment"], direction="out", limit=2
        )
        assert len(candidates) == 1
        current = await chat_attachment_upload._fresh_attachment(candidates[0])
        assert current.storage_key  # stored, but scanning is still pending
        _, _, error = await validate_chat_attachment_source(
            user_id=auth.id,
            attachment_id=candidates[0].id,
            thread_id=thread.id,
            workspace_id=thread.workspace_id,
        )
        assert error["error_code"] == "attachment_not_found"
    finally:
        release.set()
    created = await asyncio.wait_for(first, timeout=10)
    retried = await upload()
    assert retried["deduplicated"] is True
    assert retried["attachment"]["id"] == created["attachment"]["id"]


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


@pytest.mark.asyncio
async def test_reupload_after_filing_creates_a_new_chat_owned_source(monkeypatch):
    """A filed attachment's provenance edge must not turn it into a draft."""
    from app.services import chat_attachment_upload

    monkeypatch.setattr(
        chat_attachment_upload.get_metadata_extraction_service(),
        "schedule_extraction",
        lambda _: None,
    )
    thread = await _make_thread()
    first = await _persist_uploaded_chat_file(
        thread=thread, user_id="u1", file=_make_upload(b"report", "a.txt", "text/plain")
    )
    filed = await Attachment.get(first["attachment"]["id"])
    filed.owner_kind = "entry"
    await filed.save()
    second = await _persist_uploaded_chat_file(
        thread=thread, user_id="u1", file=_make_upload(b"report", "a.txt", "text/plain")
    )
    assert second["attachment"]["id"] != filed.id
    assert second["attachment"]["owner_kind"] == "chat"
    assert not second.get("deduplicated", False)


@pytest.mark.asyncio
async def test_blocked_or_unstored_chat_file_is_not_reused(monkeypatch):
    """Blocked sources and sources without a storage key are not reused."""
    from app.services import chat_attachment_upload

    monkeypatch.setattr(
        chat_attachment_upload.get_metadata_extraction_service(),
        "schedule_extraction",
        lambda _: None,
    )
    thread = await _make_thread()
    first = await _persist_uploaded_chat_file(
        thread=thread, user_id="u1", file=_make_upload(b"report", "a.txt", "text/plain")
    )
    stale = await Attachment.get(first["attachment"]["id"])
    stale.scan_status = "blocked"
    await stale.save()
    second = await _persist_uploaded_chat_file(
        thread=thread, user_id="u1", file=_make_upload(b"report", "a.txt", "text/plain")
    )
    assert second["attachment"]["id"] != stale.id
    missing = await Attachment.get(second["attachment"]["id"])
    missing.storage_key = ""
    await missing.save()
    third = await _persist_uploaded_chat_file(
        thread=thread, user_id="u1", file=_make_upload(b"report", "a.txt", "text/plain")
    )
    assert third["attachment"]["id"] not in {stale.id, missing.id}
