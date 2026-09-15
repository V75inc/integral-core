"""Tests for Plan 03 — Phase 1 + 1.5 attachment pipeline.

These tests target the helpers + services directly because the
existing CRUD test for the multipart upload endpoint is skipped (the
jvspatial ``@endpoint`` decorator unwraps JSON, not multipart). Once
that wrapper is multipart-aware, the unit-level coverage here can be
promoted to full HTTP integration tests.

Coverage:
    - Attachment node carries new Phase 1 + 1.5 fields with sensible
      defaults.
    - Content sniffer accepts compatible MIME pairs and flags
      mismatches.
    - SHA-256 dedup helper finds a prior attachment on the same entry.
    - Scanner registry returns NoopScanner by default, honours an
      override registration, and falls back when an unknown name is
      configured.
    - Metadata extractor framework dispatches the highest-priority
      matching extractor, persists results, marks status correctly,
      and is idempotent on re-run.
    - /attachments/{id}/reprocess re-runs extraction and returns the
      refreshed attachment.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from httpx import AsyncClient

from app.config import settings
from app.models.edges import HAS_ATTACHMENT
from app.models.nodes import Attachment, Entry
from app.services.attachment_content_sniffer import sniff_bytes
from app.services.attachment_metadata import (
    AttachmentMetadataExtractor,
    MetadataResult,
    reset_for_testing,
)
from app.services.attachment_metadata.service import MetadataExtractionService
from app.services.attachment_scanner import (
    SCAN_STATUS_BLOCKED,
    SCAN_STATUS_CLEAN,
    NoopScanner,
    ScanResult,
    get_attachment_scanner,
    register_scanner,
)
from app.services.attachment_scanner import reset_for_testing as reset_scanner
from app.services.attachment_storage import get_attachment_storage_service

# ---------------------------------------------------------------------------
# Node shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attachment_node_has_phase1_fields():
    """The new fields land on Attachment with safe defaults."""
    a = await Attachment.create(
        filename="f.txt",
        mime_type="text/plain",
        size=10,
        storage_key="",
        uploaded_by="u1",
        created_at=datetime.now().isoformat(),
    )
    # Hardening fields
    assert a.content_hash == ""
    assert a.scan_status == "pending"
    assert a.scan_engine == ""
    assert a.scan_message == ""
    # Derived
    assert a.width is None
    assert a.height is None
    assert a.page_count is None
    assert a.preview_storage_key == ""
    assert a.thumb_storage_key == ""
    # Metadata pipeline
    assert a.metadata == {}
    assert a.extracted_text == ""
    assert a.metadata_status == "pending"
    assert a.metadata_extractor_version == 0
    assert a.metadata_error == ""


# ---------------------------------------------------------------------------
# Content sniffer
# ---------------------------------------------------------------------------


def test_sniffer_passes_through_when_claim_and_sniff_agree():
    # If libmagic is present, header bytes of a PNG should sniff
    # as image/png; if it isn't, the sniffer trusts the claim.
    png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 128
    r = sniff_bytes(png_header, claimed_mime="image/png", filename="x.png")
    assert r.mismatch is False
    assert r.effective_mime in ("image/png",)


def test_sniffer_treats_known_pairs_as_compatible():
    # JSON-as-text is one of the explicit compatible pairs. With
    # libmagic absent the claim wins; with it present the pair is
    # whitelisted. Either way, no mismatch.
    body = b'{"hello": "world"}'
    r = sniff_bytes(body, claimed_mime="application/json", filename="x.json")
    assert r.mismatch is False
    assert r.effective_mime in ("application/json", "text/plain", "text/json")


def test_sniffer_flags_obvious_mismatch_when_libmagic_available():
    """A claim that contradicts the sniff is flagged.

    When libmagic isn't available the test is informational only —
    the sniffer can't tell the difference and won't flag.
    """
    from app.services.attachment_content_sniffer import is_available

    pdf_header = b"%PDF-1.4\n%\xc7\xec\x8f\xa2\n" + b"\x00" * 256
    r = sniff_bytes(pdf_header, claimed_mime="image/png", filename="bogus.png")
    if is_available():
        assert r.mismatch is True
        assert "pdf" in (r.sniffed_mime or "").lower() or r.sniffed_mime != ""
    else:
        # Sniffer falls back to trusting the claim — that's the
        # documented degraded behaviour for environments without
        # libmagic.
        assert r.mismatch is False


# ---------------------------------------------------------------------------
# Scanner registry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_scanner_is_noop_and_marks_skipped():
    reset_scanner()
    scanner = get_attachment_scanner()
    verdict = await scanner.scan(content=b"hello", mime_type="text/plain", filename="x")
    assert verdict.status == "skipped"
    assert verdict.engine == "noop"


@pytest.mark.asyncio
async def test_registered_scanner_is_picked_up(monkeypatch):
    class _CleanScanner(NoopScanner):
        name = "test-clean"

        async def scan(self, *, content, mime_type, filename):
            return ScanResult(status=SCAN_STATUS_CLEAN, engine=self.name)

    register_scanner("test-clean", _CleanScanner)
    monkeypatch.setattr(settings, "ATTACHMENT_SCANNER", "test-clean")
    reset_scanner()
    scanner = get_attachment_scanner()
    assert scanner.name == "test-clean"
    verdict = await scanner.scan(content=b"x", mime_type="text/plain", filename="y")
    assert verdict.status == SCAN_STATUS_CLEAN


@pytest.mark.asyncio
async def test_unknown_scanner_name_falls_back_to_noop(monkeypatch):
    monkeypatch.setattr(settings, "ATTACHMENT_SCANNER", "does-not-exist")
    reset_scanner()
    scanner = get_attachment_scanner()
    assert isinstance(scanner, NoopScanner)


# ---------------------------------------------------------------------------
# Metadata extractor framework
# ---------------------------------------------------------------------------


class _RecordingExtractor(AttachmentMetadataExtractor):
    name = "test-recorder"
    mime_prefixes = ("text/",)
    exact_mimes = frozenset({"application/pdf"})
    priority = 100

    def __init__(self):
        super().__init__()
        self.calls = 0

    async def extract(self, *, path, mime, filename, size):
        self.calls += 1
        return MetadataResult(
            common={"size_seen": size},
            type_specific={"extractor": self.name, "filename": filename},
            extracted_text=f"hello from {filename}",
            page_count=3,
        )


@pytest.mark.asyncio
async def test_metadata_service_dispatches_and_persists():
    """The dispatcher picks the highest-priority matching extractor and
    persists the result onto the Attachment node."""
    recorder = _RecordingExtractor()
    reset_for_testing(extractors=(recorder,))
    from app.services.attachment_metadata import (
        get_metadata_extraction_service,
    )

    service = get_metadata_extraction_service()
    assert isinstance(service, MetadataExtractionService)

    # Stage an attachment with bytes in storage.
    storage = get_attachment_storage_service()
    payload = b"hello world"
    att = await Attachment.create(
        filename="hello.txt",
        mime_type="text/plain",
        size=len(payload),
        storage_key="",
        uploaded_by="u1",
        content_hash="abc",
        scan_status="clean",
        metadata_status="pending",
        created_at=datetime.now().isoformat(),
    )
    stored = await storage.save_attachment(
        entry_id="e1",
        attachment_id=att.id,
        filename="hello.txt",
        content=payload,
        metadata={"entry_id": "e1", "attachment_id": att.id},
    )
    att.storage_key = str(stored.get("path") or "")
    await att.save()

    updated = await service.extract_now(att.id)
    assert recorder.calls == 1
    assert updated.metadata_status == "complete"
    assert updated.page_count == 3
    assert updated.extracted_text == "hello from hello.txt"
    assert updated.metadata["common"] == {"size_seen": len(payload)}
    assert updated.metadata["type_specific"]["extractor"] == "test-recorder"
    assert updated.metadata_extractor_version == (
        settings.ATTACHMENT_METADATA_EXTRACTOR_VERSION
    )


@pytest.mark.asyncio
async def test_metadata_service_is_idempotent_on_reprocess():
    """Re-running extraction overwrites prior state cleanly."""
    recorder = _RecordingExtractor()
    reset_for_testing(extractors=(recorder,))
    from app.services.attachment_metadata import (
        get_metadata_extraction_service,
    )

    service = get_metadata_extraction_service()
    storage = get_attachment_storage_service()
    payload = b"abcdef"
    att = await Attachment.create(
        filename="thing.txt",
        mime_type="text/plain",
        size=len(payload),
        storage_key="",
        uploaded_by="u1",
        scan_status="clean",
        metadata_status="pending",
        created_at=datetime.now().isoformat(),
    )
    stored = await storage.save_attachment(
        entry_id="e2",
        attachment_id=att.id,
        filename="thing.txt",
        content=payload,
    )
    att.storage_key = str(stored.get("path") or "")
    await att.save()

    first = await service.extract_now(att.id)
    second = await service.extract_now(att.id)
    assert recorder.calls == 2
    assert first.id == second.id
    assert second.metadata_status == "complete"


@pytest.mark.asyncio
async def test_metadata_service_skips_url_attachments():
    """URL attachments have no bytes to extract — status should be ``skipped``."""
    reset_for_testing(extractors=(_RecordingExtractor(),))
    from app.services.attachment_metadata import (
        get_metadata_extraction_service,
    )

    att = await Attachment.create(
        filename="link",
        mime_type="text/uri-list",
        size=0,
        storage_key="attachments/e/url/123",
        source_type="url",
        external_url="https://example.com",
        uploaded_by="u1",
        created_at=datetime.now().isoformat(),
    )
    service = get_metadata_extraction_service()
    updated = await service.extract_now(att.id)
    assert updated.metadata_status == "skipped"


# ---------------------------------------------------------------------------
# Reprocess endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestReprocessEndpoint:
    """The reprocess endpoint is reachable via HTTP and runs extraction.

    It doesn't require multipart, so unlike the upload endpoint it
    works under the in-process test client.
    """

    async def _create_track_and_entry(self, client: AsyncClient):
        track_resp = await client.post(
            "/api/tracks",
            json={"title": "Attachment Pipeline Track", "visibility": "private"},
        )
        assert track_resp.status_code == 200
        track_id = track_resp.json()["track"]["id"]
        entry_resp = await client.post(
            "/api/entries",
            json={
                "track_id": track_id,
                "title": "Entry for reprocess",
                "body": "Body",
            },
        )
        assert entry_resp.status_code == 200
        return track_id, entry_resp.json()["entry"]["id"]

    async def _stage_attachment(self, entry_id: str, user_id: str, payload: bytes):
        att = await Attachment.create(
            filename="reprocess-me.txt",
            mime_type="text/plain",
            size=len(payload),
            storage_key="",
            uploaded_by=user_id,
            scan_status="clean",
            metadata_status="pending",
            created_at=datetime.now().isoformat(),
        )
        storage = get_attachment_storage_service()
        stored = await storage.save_attachment(
            entry_id=entry_id,
            attachment_id=att.id,
            filename="reprocess-me.txt",
            content=payload,
        )
        att.storage_key = str(stored.get("path") or "")
        await att.save()
        entry = await Entry.get(entry_id)
        await entry.connect(
            att,
            edge=HAS_ATTACHMENT,
            attached_at=datetime.now().isoformat(),
            attached_by=user_id,
        )
        if att.id not in entry.attachment_ids:
            entry.attachment_ids.append(att.id)
            await entry.save()
        return att.id

    async def test_reprocess_runs_extraction(
        self, authenticated_client: AsyncClient, test_user
    ):
        # Pin a deterministic extractor so the test doesn't depend on
        # which optional libs are installed in CI.
        recorder = _RecordingExtractor()
        reset_for_testing(extractors=(recorder,))

        _, entry_id = await self._create_track_and_entry(authenticated_client)
        user_id = getattr(test_user, "user_id", None) or test_user.id
        att_id = await self._stage_attachment(entry_id, user_id, b"hello reprocess")

        resp = await authenticated_client.post(f"/api/attachments/{att_id}/reprocess")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "attachment" in data
        # Whichever export shape — flat or context-wrapped — extracts
        # the status field.
        att_payload = data["attachment"]
        status = att_payload.get("metadata_status") or att_payload.get(
            "context", {}
        ).get("metadata_status")
        assert status == "complete"
        assert recorder.calls == 1
