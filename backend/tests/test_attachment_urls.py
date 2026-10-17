"""Tests for shared attachment URL enrichment."""

from urllib.parse import parse_qs

import pytest

from app.models.nodes import Attachment
from app.services.attachment_urls import (
    enrich_attachment_export,
    is_visible_to_user,
    mint_attachment_token,
    thumb_url_for,
    verify_attachment_token,
)


def test_signed_attachment_token_binds_id_expiry_and_signature(monkeypatch):
    monkeypatch.setattr("app.services.attachment_urls.time.time", lambda: 1000)
    query = parse_qs(mint_attachment_token("att-token", ttl=60))
    exp, sig = query["exp"][0], query["sig"][0]
    assert verify_attachment_token("att-token", exp, sig)
    assert not verify_attachment_token("other", exp, sig)
    assert not verify_attachment_token("att-token", "1061", sig)
    assert not verify_attachment_token("att-token", exp, "0" * 64)
    assert not verify_attachment_token("att-token", None, sig)
    assert not verify_attachment_token("att-token", exp, "invalid-✓")
    monkeypatch.setattr("app.services.attachment_urls.time.time", lambda: 1061)
    assert not verify_attachment_token("att-token", exp, sig)


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
    # Session-free signed URL served by Core's own storage (local provider).
    assert item["download_url"].startswith("/api/attachments/att-2/inline?exp=")
    assert "&sig=" in item["download_url"]
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
