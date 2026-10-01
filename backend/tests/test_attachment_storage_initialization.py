"""Storage failures must not persist detached attachment nodes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api import attachments


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["entry", "chat", "chunked"])
async def test_storage_initialization_precedes_attachment_create(monkeypatch, path):
    async def no_duplicate(*_args, **_kwargs):
        return None

    monkeypatch.setattr(attachments, "_find_duplicate_hash_on_entry", no_duplicate)
    monkeypatch.setattr(attachments, "_find_duplicate_hash_on_thread", no_duplicate)
    monkeypatch.setattr(attachments, "check_quota_for_entry", no_duplicate)
    monkeypatch.setattr(attachments, "check_workspace_quota", no_duplicate)
    monkeypatch.setattr(
        attachments,
        "_read_upload_streaming",
        AsyncMock(return_value=(b"fixture", "fixture.txt", "hash", b"fixture")),
    )
    monkeypatch.setattr(
        attachments, "_resolve_effective_mime", lambda **_kwargs: "text/plain"
    )
    create = AsyncMock(side_effect=AssertionError("Attachment.create was called"))
    monkeypatch.setattr(attachments.Attachment, "create", create)

    def unavailable_storage():
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr(
        attachments, "get_attachment_storage_service", unavailable_storage
    )
    entry = SimpleNamespace(id="entry-1")
    thread = SimpleNamespace(id="thread-1", workspace_id="workspace-1")
    upload = SimpleNamespace(content_type="text/plain")

    with pytest.raises(RuntimeError, match="storage unavailable"):
        if path == "entry":
            await attachments._persist_uploaded_file(
                entry=entry, user_id="user-1", file=upload
            )
        elif path == "chat":
            await attachments._persist_uploaded_chat_file(
                thread=thread, user_id="user-1", file=upload
            )
        else:
            await attachments._persist_assembled_content(
                entry=entry,
                user_id="user-1",
                filename="fixture.txt",
                content=b"fixture",
                declared_mime="text/plain",
                sha256_hex="hash",
            )

    create.assert_not_called()
