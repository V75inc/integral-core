"""Regression: jvspatial save_file needs metadata["mime"] hint.

Without the hint, Office docs often sniff as application/octet-stream and
the local storage allow-list rejects the write with a 500.
"""

from __future__ import annotations

import pytest

from app.services.attachment_storage import AttachmentStorageService


class _CapturingStorage:
    def __init__(self) -> None:
        self.last_metadata = None

    async def save_file(self, key, content, metadata=None):
        self.last_metadata = metadata
        return {"path": key, "size": len(content)}


@pytest.mark.asyncio
async def test_save_attachment_injects_docx_mime_from_filename():
    backend = _CapturingStorage()
    svc = AttachmentStorageService(storage=backend)
    await svc.save_attachment(
        entry_id="entry-1",
        attachment_id="att-1",
        filename="Proposal_Template.docx",
        content=b"PK\x03\x04" + b"\x00" * 32,
    )
    assert backend.last_metadata is not None
    assert (
        backend.last_metadata["mime"]
        == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )


@pytest.mark.asyncio
async def test_save_attachment_prefers_explicit_mime_type():
    backend = _CapturingStorage()
    svc = AttachmentStorageService(storage=backend)
    await svc.save_attachment(
        entry_id="entry-1",
        attachment_id="att-1",
        filename="weird.bin",
        content=b"hello",
        mime_type="application/pdf",
    )
    assert backend.last_metadata["mime"] == "application/pdf"
