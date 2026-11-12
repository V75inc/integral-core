"""Chat-owned Attachment must be graph-reachable via its ChatThread (I-GRAPH-01).

Slice B (chat file upload) reuses the Attachment node + HAS_ATTACHMENT edge
for chat-thread-scoped files instead of introducing a parallel node/edge
pair. ``owner_kind`` distinguishes chat-owned rows from entry-owned rows;
the edge source is a ``ChatThread`` instead of an ``Entry``.
"""

import pytest

from app.models.edges import HAS_ATTACHMENT
from app.models.nodes import Attachment, ChatThread
from app.services import chat_threads as chat_store


@pytest.mark.asyncio
async def test_attachment_owner_kind_defaults_to_entry():
    """Back-compat: existing entry-attachment call sites don't set owner_kind."""
    attachment = await Attachment.create(
        filename="doc.pdf", mime_type="application/pdf"
    )
    assert attachment.owner_kind == "entry"


@pytest.mark.asyncio
async def test_chat_attachment_wired_from_thread():
    """A chat-owned Attachment attaches to its ChatThread via HAS_ATTACHMENT."""
    thread = await chat_store.create_thread(
        user_id="u1",
        provider_id="integral_native",
        workspace_id="ws1",
    )
    attachment = await Attachment.create(
        filename="report.csv",
        mime_type="text/csv",
        owner_kind="chat",
    )
    await thread.connect(attachment, edge=HAS_ATTACHMENT)

    children = await thread.nodes(
        edge=[HAS_ATTACHMENT],
        node=["Attachment"],
        direction="out",
    )
    assert any(a.id == attachment.id for a in children)


@pytest.mark.asyncio
async def test_chat_attachment_reachable_from_thread_reverse_edge():
    """The attachment can find its owning thread by walking the edge in."""
    thread = await chat_store.create_thread(
        user_id="u1",
        provider_id="integral_native",
        workspace_id="ws1",
    )
    attachment = await Attachment.create(
        filename="image.png",
        mime_type="image/png",
        owner_kind="chat",
    )
    await thread.connect(attachment, edge=HAS_ATTACHMENT)

    owners = await attachment.nodes(
        edge=[HAS_ATTACHMENT],
        node=["ChatThread"],
        direction="in",
    )
    assert len(owners) == 1
    assert isinstance(owners[0], ChatThread)
    assert owners[0].id == thread.id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid",
    [
        None,
        "user",
        "workspace",
        "archived",
        "revoked",
        "scan",
        "pending",
        "quota_rejected",
        "other_thread",
        "entry_owned",
    ],
)
async def test_retained_chat_references_require_current_scope_and_source(
    monkeypatch, invalid
):
    from unittest.mock import AsyncMock

    from app.services.chat_turn_attachments import retained_chat_attachment_references

    monkeypatch.setattr(
        "app.services.workspace_permissions.can_access_workspace",
        AsyncMock(return_value="none" if invalid == "revoked" else "owner"),
    )
    thread = await ChatThread.create(
        user_id="u1", workspace_id="ws1", archived=invalid == "archived"
    )
    other = await ChatThread.create(user_id="u1", workspace_id="ws1")
    attachment = await Attachment.create(
        filename="private.pdf",
        mime_type="application/pdf",
        size=42,
        uploaded_by="u1",
        owner_kind="entry" if invalid == "entry_owned" else "chat",
        scan_status=(
            "blocked"
            if invalid == "scan"
            else invalid if invalid in {"pending", "quota_rejected"} else "skipped"
        ),
        storage_key="never-disclose",
        extracted_text="private body",
        content_hash="a" * 64,
    )
    await (other if invalid == "other_thread" else thread).connect(
        attachment, edge=HAS_ATTACHMENT
    )
    refs = await retained_chat_attachment_references(
        thread=thread,
        principal_id="u2" if invalid == "user" else "u1",
        workspace_id="ws2" if invalid == "workspace" else "ws1",
    )
    if invalid:
        assert refs == {"files": [], "truncated": False}
    else:
        assert refs == {
            "files": [
                {
                    "attachment_id": attachment.id,
                    "filename": "private.pdf",
                    "mime_type": "application/pdf",
                    "size": 42,
                }
            ],
            "truncated": False,
        }
        assert "private body" not in str(refs) and "never-disclose" not in str(refs)
        assert await retained_chat_attachment_references(
            thread=thread,
            principal_id="u1",
            workspace_id="ws1",
            exclude_ids={attachment.id},
        ) == {"files": [], "truncated": False}


@pytest.mark.asyncio
async def test_retained_chat_references_are_bounded_and_reject_ambiguous_owners(
    monkeypatch,
):
    from unittest.mock import AsyncMock

    from app.services.chat_turn_attachments import retained_chat_attachment_references

    monkeypatch.setattr(
        "app.services.workspace_permissions.can_access_workspace",
        AsyncMock(return_value="owner"),
    )
    thread = await ChatThread.create(user_id="u1", workspace_id="ws1")
    other = await ChatThread.create(user_id="u2", workspace_id="ws1")
    for i in range(22):
        attachment = await Attachment.create(
            filename=f"file-{i}.pdf",
            storage_key=f"chat/file-{i}",
            mime_type="application/pdf",
            uploaded_by="u1",
            owner_kind="chat",
            scan_status="skipped",
        )
        await thread.connect(attachment, edge=HAS_ATTACHMENT)
        if i == 0:
            await other.connect(attachment, edge=HAS_ATTACHMENT)
            ambiguous_id = attachment.id
    refs = await retained_chat_attachment_references(
        thread=thread, principal_id="u1", workspace_id="ws1"
    )
    assert refs["truncated"] is True
    assert 19 <= len(refs["files"]) <= 20
    assert ambiguous_id not in {file["attachment_id"] for file in refs["files"]}


@pytest.mark.asyncio
async def test_retained_chat_reference_metadata_is_bounded(monkeypatch):
    from unittest.mock import AsyncMock

    from app.services.chat_turn_attachments import retained_chat_attachment_references

    monkeypatch.setattr(
        "app.services.workspace_permissions.can_access_workspace",
        AsyncMock(return_value="owner"),
    )
    thread = await ChatThread.create(user_id="u1", workspace_id="ws1")
    attachment = await Attachment.create(
        filename="x" * 10000,
        mime_type="y" * 10000,
        uploaded_by="u1",
        owner_kind="chat",
        scan_status="skipped",
        storage_key="private",
    )
    await thread.connect(attachment, edge=HAS_ATTACHMENT)
    refs = await retained_chat_attachment_references(
        thread=thread, principal_id="u1", workspace_id="ws1"
    )
    assert len(refs["files"][0]["filename"]) == 512
    assert len(refs["files"][0]["mime_type"]) == 128


@pytest.mark.asyncio
async def test_current_upload_content_is_bounded_and_not_historical(monkeypatch):
    from unittest.mock import AsyncMock

    from app.services.chat_turn_attachments import current_chat_attachment_content

    monkeypatch.setattr(
        "app.services.workspace_permissions.can_access_workspace",
        AsyncMock(return_value="owner"),
    )
    thread = await ChatThread.create(user_id="u1", workspace_id="ws1")
    parts = []
    for i in range(4):
        attachment = await Attachment.create(
            filename=f"file-{i}.docx",
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            size=42,
            storage_key=f"chat/{i}",
            content_hash="a" * 64,
            uploaded_by="u1",
            owner_kind="chat",
            scan_status="skipped",
            metadata_status="complete",
            extracted_text="X" * 15000,
        )
        await thread.connect(attachment, edge=HAS_ATTACHMENT)
        if i < 3:
            parts.append(
                {
                    "type": "file",
                    "attachment_id": attachment.id,
                    "filename": attachment.filename,
                    "mime_type": attachment.mime_type,
                    "size": attachment.size,
                }
            )
        else:
            historical_id = attachment.id
    result = await current_chat_attachment_content(
        thread=thread, principal_id="u1", workspace_id="ws1", parts=parts
    )
    assert len(result["files"]) == 2
    assert sum(len(f["text"]) for f in result["files"]) == 24000
    assert all(f["truncated"] and f["content_untrusted"] for f in result["files"])
    assert result["truncated"] is True
    assert historical_id not in str(result)
    assert "storage_key" not in str(result)
    assert await current_chat_attachment_content(
        thread=thread,
        principal_id="u1",
        workspace_id="ws1",
        parts=[{"type": "text", "text": historical_id}],
    ) == {"files": [], "truncated": False}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid",
    [
        "foreign_thread",
        "workspace",
        "revoked",
        "blocked",
        "metadata_changed",
        "multiple_owners",
    ],
)
async def test_current_upload_content_fails_closed(monkeypatch, invalid):
    from unittest.mock import AsyncMock

    from app.schemas.agentive.work import WorkError
    from app.services.chat_turn_attachments import current_chat_attachment_content

    monkeypatch.setattr(
        "app.services.workspace_permissions.can_access_workspace",
        AsyncMock(return_value="none" if invalid == "revoked" else "owner"),
    )
    thread = await ChatThread.create(user_id="u1", workspace_id="ws1")
    other = await ChatThread.create(user_id="u2", workspace_id="ws1")
    attachment = await Attachment.create(
        filename="source.docx",
        mime_type="application/docx",
        size=42,
        storage_key="chat/source",
        content_hash="a" * 64,
        uploaded_by="u1",
        owner_kind="chat",
        scan_status="blocked" if invalid == "blocked" else "skipped",
        metadata_status="complete",
        extracted_text="Source facts",
    )
    await (other if invalid == "foreign_thread" else thread).connect(
        attachment, edge=HAS_ATTACHMENT
    )
    if invalid == "multiple_owners":
        await other.connect(attachment, edge=HAS_ATTACHMENT)
    part = {
        "type": "file",
        "attachment_id": attachment.id,
        "filename": "changed" if invalid == "metadata_changed" else attachment.filename,
        "mime_type": attachment.mime_type,
        "size": attachment.size,
    }
    with pytest.raises(WorkError):
        await current_chat_attachment_content(
            thread=thread,
            principal_id="u1",
            workspace_id="ws2" if invalid == "workspace" else "ws1",
            parts=[part],
        )


@pytest.mark.asyncio
async def test_current_upload_content_preserves_empty_extraction_and_skips_image_pixels(
    monkeypatch,
):
    from unittest.mock import AsyncMock

    from app.services.chat_turn_attachments import current_chat_attachment_content

    monkeypatch.setattr(
        "app.services.workspace_permissions.can_access_workspace",
        AsyncMock(return_value="owner"),
    )
    thread = await ChatThread.create(user_id="u1", workspace_id="ws1")
    parts = []
    for mime in ["application/pdf", "image/png"]:
        attachment = await Attachment.create(
            filename="source",
            mime_type=mime,
            size=42,
            storage_key="chat/source",
            content_hash="a" * 64,
            uploaded_by="u1",
            owner_kind="chat",
            scan_status="skipped",
            metadata_status="failed",
            extracted_text="",
        )
        await thread.connect(attachment, edge=HAS_ATTACHMENT)
        parts.append(
            {
                "type": "file",
                "attachment_id": attachment.id,
                "filename": attachment.filename,
                "mime_type": mime,
                "size": attachment.size,
            }
        )
    result = await current_chat_attachment_content(
        thread=thread, principal_id="u1", workspace_id="ws1", parts=parts
    )
    assert len(result["files"]) == 1
    assert result["files"][0]["metadata_status"] == "failed"
    assert result["files"][0]["text"] == ""
