"""CRUD tests for Attachments API."""

from datetime import datetime

import pytest
from httpx import AsyncClient

from app.models.edges import HAS_ATTACHMENT
from app.models.nodes import Attachment, Entry
from app.services.attachment_storage import get_attachment_storage_service


@pytest.mark.asyncio
class TestAttachmentsCRUD:
    """Test suite for Attachments CRUD operations."""

    async def _create_track(
        self, client: AsyncClient, title: str = "Attachment Test Track"
    ):
        response = await client.post(
            "/api/tracks", json={"title": title, "visibility": "private"}
        )
        assert response.status_code == 200, f"Track creation failed: {response.text}"
        return response.json()["track"]["id"]

    async def _create_entry(
        self, client: AsyncClient, track_id: str, title: str = "Entry for attachments"
    ):
        response = await client.post(
            "/api/entries",
            json={"track_id": track_id, "title": title, "body": "Test body"},
        )
        assert response.status_code == 200, f"Entry creation failed: {response.text}"
        return response.json()["entry"]["id"]

    async def _create_attachment_programmatically(
        self,
        entry_id: str,
        user_id: str,
        filename: str = "test.txt",
        content: bytes = b"content",
        persist_blob: bool = False,
    ) -> str:
        """Create attachment via model (bypasses upload API for testing get/delete)."""
        now = datetime.now().isoformat()
        attachment = await Attachment.create(
            filename=filename,
            mime_type="text/plain",
            size=len(content),
            storage_key="",
            uploaded_by=user_id,
            created_at=now,
        )
        if persist_blob:
            storage = get_attachment_storage_service()
            stored = await storage.save_attachment(
                entry_id=entry_id,
                attachment_id=attachment.id,
                filename=filename,
                content=content,
                metadata={"entry_id": entry_id, "attachment_id": attachment.id},
            )
            attachment.storage_key = str(stored.get("path") or "")
            await attachment.save()
        else:
            attachment.storage_key = f"attachments/{entry_id}/{filename}"
            await attachment.save()
        entry = await Entry.get(entry_id)
        if entry:
            await entry.connect(
                attachment,
                edge=HAS_ATTACHMENT,
                attached_at=now,
                attached_by=user_id,
            )
        return attachment.id

    async def test_upload_attachment(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test uploading a file attachment to an entry.

        Handler parses multipart via ``request.form()``
        (``_read_multipart_files``) — same pattern as avatar upload tests.
        """
        track_id = await self._create_track(authenticated_client)
        entry_id = await self._create_entry(authenticated_client, track_id)

        files = {"file": ("test.txt", b"Hello, this is test content", "text/plain")}

        response = await authenticated_client.post(
            f"/api/entries/{entry_id}/attachments",
            files=files,
        )

        assert response.status_code == 200, response.text
        data = response.json()
        assert "attachment" in data
        assert data["attachment"]["filename"] == "test.txt"

    async def test_get_attachment(self, authenticated_client: AsyncClient, test_user):
        """Test getting attachment metadata."""
        track_id = await self._create_track(authenticated_client)
        entry_id = await self._create_entry(authenticated_client, track_id)
        user_id = getattr(test_user, "user_id", None) or test_user.id
        attachment_id = await self._create_attachment_programmatically(
            entry_id, user_id, "doc.pdf", b"PDF content"
        )

        response = await authenticated_client.get(f"/api/attachments/{attachment_id}")

        assert response.status_code == 200
        data = response.json()
        assert "attachment" in data
        att = data["attachment"]
        assert att.get("id") == attachment_id
        filename = att.get("filename") or att.get("context", {}).get("filename")
        assert filename == "doc.pdf"
        assert "download_url" in data
        assert data["download_url"] == f"/api/attachments/{attachment_id}/download"

    async def test_stream_attachment_file(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test streaming stored attachment bytes."""
        track_id = await self._create_track(authenticated_client)
        entry_id = await self._create_entry(authenticated_client, track_id)
        user_id = getattr(test_user, "user_id", None) or test_user.id
        payload = b"Hello attachment bytes"
        attachment_id = await self._create_attachment_programmatically(
            entry_id,
            user_id,
            "stream.txt",
            payload,
            persist_blob=True,
        )

        response = await authenticated_client.get(
            f"/api/attachments/{attachment_id}/download"
        )
        assert response.status_code == 200
        assert response.content == payload
        assert response.headers.get("content-type", "").startswith("text/plain")

    async def test_delete_attachment(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test deleting an attachment."""
        track_id = await self._create_track(authenticated_client)
        entry_id = await self._create_entry(authenticated_client, track_id)
        user_id = getattr(test_user, "user_id", None) or test_user.id
        attachment_id = await self._create_attachment_programmatically(
            entry_id, user_id, "delete-me.txt", b"Delete me", persist_blob=True
        )
        attachment = await Attachment.get(attachment_id)
        assert attachment is not None
        storage_key = attachment.storage_key

        response = await authenticated_client.delete(
            f"/api/attachments/{attachment_id}"
        )

        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert data["deleted_attachment_id"] == attachment_id

        get_response = await authenticated_client.get(
            f"/api/attachments/{attachment_id}"
        )
        assert get_response.status_code == 404
        storage = get_attachment_storage_service()
        blob = await storage.read_attachment(storage_key)
        assert blob is None

    async def test_get_nonexistent_attachment(self, authenticated_client: AsyncClient):
        """Test getting an attachment that doesn't exist."""
        response = await authenticated_client.get("/api/attachments/nonexistent-id")
        assert response.status_code == 404

    async def test_create_url_attachment(
        self, authenticated_client: AsyncClient, test_user
    ):
        """Test adding a URL attachment to an entry."""
        track_id = await self._create_track(authenticated_client)
        entry_id = await self._create_entry(authenticated_client, track_id)
        # Public literal IP avoids DNS (CI/sandbox-friendly); SSRF checks still apply.
        response = await authenticated_client.post(
            f"/api/entries/{entry_id}/attachments/url",
            json={
                "url": "http://8.8.8.8/article",
                "label": "Example Article",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "attachment" in data
        att = data["attachment"]
        source_type = att.get("source_type") or att.get("context", {}).get(
            "source_type"
        )
        external_url = att.get("external_url") or att.get("context", {}).get(
            "external_url"
        )
        assert source_type == "url"
        assert external_url == "http://8.8.8.8/article"

    async def test_upload_attachment_nonexistent_entry(
        self, authenticated_client: AsyncClient
    ):
        """Upload to a missing entry is refused (policy fail-closed → 403)."""
        files = {"file": ("test.txt", b"content", "text/plain")}
        response = await authenticated_client.post(
            "/api/entries/nonexistent-id/attachments",
            files=files,
        )
        # Policy evaluate runs before Entry.get; missing resources deny as 403.
        assert response.status_code == 403
