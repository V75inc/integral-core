"""Tests for shared attachment URL enrichment."""

import pytest

from app.models.nodes import Attachment
from app.services.attachment_urls import (
    enrich_attachment_export,
    is_visible_to_user,
    thumb_url_for,
)


def test_is_visible_to_user_hides_blocked():
    att = Attachment(filename="x.png", scan_status="blocked")
    assert is_visible_to_user(att) is False
    att.scan_status = "clean"
    assert is_visible_to_user(att) is True


def test_thumb_url_for_file_with_thumb_key():
    att = Attachment(
        id="att-1",
        filename="photo.png",
        source_type="file",
        mime_type="image/png",
        thumb_storage_key="attachments/att-1_thumb.jpg",
    )
    assert thumb_url_for(att) == "/api/attachments/att-1/thumb"


@pytest.mark.asyncio
async def test_enrich_attachment_export_adds_urls_for_file_image():
    att = Attachment(
        id="att-2",
        filename="photo.png",
        source_type="file",
        mime_type="image/png",
        storage_key="attachments/att-2.png",
        thumb_storage_key="attachments/att-2_thumb.jpg",
    )
    item = {"id": att.id, "filename": att.filename}
    await enrich_attachment_export(item, att)
    assert item["download_url"] == "/api/attachments/att-2/download"
    assert item["thumb_url"] == "/api/attachments/att-2/thumb"


@pytest.mark.asyncio
async def test_enrich_attachment_export_uses_external_url_for_link():
    att = Attachment(
        id="att-3",
        filename="link",
        source_type="url",
        external_url="https://cdn.example.com/a.png",
    )
    item = {"id": att.id}
    await enrich_attachment_export(item, att)
    assert item["download_url"] == "https://cdn.example.com/a.png"
    assert item["thumb_url"] is None
