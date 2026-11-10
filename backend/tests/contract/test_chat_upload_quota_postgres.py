"""Chat uploads and generated drafts coordinate quota at publication."""

import asyncio
import io
from unittest.mock import AsyncMock, MagicMock

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async
from jvspatial.storage import create_storage
from starlette.datastructures import Headers, UploadFile

from app.agentive import artifacts
from app.api.errors import BadRequestError
from app.config import settings
from app.models.nodes import Attachment
from app.services import (
    attachment_storage,
    chat_attachment_upload,
    chat_upload_finalization,
    document_draft_preparation,
)
from app.services.app_operations import event_outbox
from app.services.attachment_agent import validate_chat_attachment_source
from app.services.chat_threads import create_thread
from app.services.document_draft_access import load_document_draft_snapshot
from app.services.document_draft_preparation import prepare_document_draft
from app.services.document_drafts import render_document_draft
from app.services.workspace_storage_usage import get_usage
from tests.contract.test_staging_command_apply_postgres import proposal

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


async def test_chat_upload_checks_quota_after_concurrent_generated_publication(
    postgres_raw_db, monkeypatch, tmp_path
):
    """An in-flight upload cannot publish after another file consumes its quota."""
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "22" * 32)
    monkeypatch.setattr(settings, "STORAGE_QUOTA_ENFORCE", True)
    monkeypatch.setattr(
        document_draft_preparation, "_schedule_pending_metadata", AsyncMock()
    )
    storage = attachment_storage.AttachmentStorageService(
        create_storage(
            "local",
            root_dir=str(tmp_path),
            allowed_mime_types=attachment_storage._storage_allowed_mime_types(),
            create_root=True,
        )
    )
    monkeypatch.setattr(attachment_storage, "_ATTACHMENT_STORAGE_SERVICE", storage)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, _, _ = await proposal()
        upload_thread = await create_thread(
            user_id=auth.id, provider_id="integral_native", workspace_id=workspace.id
        )
        draft_thread = await create_thread(
            user_id=auth.id, provider_id="integral_native", workspace_id=workspace.id
        )
        await artifacts.upsert_artifact(
            user_id=auth.id,
            workspace_id=workspace.id,
            session_id=draft_thread.id,
            key="report",
            kind="document_draft",
            title="Report",
            body="A generated report.",
            metadata={"format": "markdown"},
            expected_version=0,
        )
        snapshot = await load_document_draft_snapshot(
            user_id=auth.id,
            workspace_id=workspace.id,
            thread_id=draft_thread.id,
            key="report",
            version=1,
        )
        draft_bytes = render_document_draft(
            title=snapshot.title, body=snapshot.body, format=snapshot.format
        ).content
        upload_bytes = b"Separate user-supplied file."
        await postgres_raw_db.find_one_and_update(
            "node",
            {"id": workspace.id},
            {
                "$set": {
                    "context.storage_quota_bytes": len(draft_bytes)
                    + len(upload_bytes)
                    - 1
                }
            },
        )
        scanner = chat_attachment_upload.get_attachment_scanner()
        entered, release = asyncio.Event(), asyncio.Event()

        class PausedScanner:
            name = scanner.name

            async def scan(self, **kwargs):
                if kwargs["content"] == upload_bytes:
                    entered.set()
                    await release.wait()
                return await scanner.scan(**kwargs)

        monkeypatch.setattr(
            chat_attachment_upload, "get_attachment_scanner", lambda: PausedScanner()
        )
        upload = asyncio.create_task(
            chat_attachment_upload.persist_chat_upload(
                thread=upload_thread,
                user_id=auth.id,
                file=UploadFile(
                    file=io.BytesIO(upload_bytes),
                    filename="upload.txt",
                    headers=Headers({"content-type": "text/plain"}),
                ),
            )
        )
        try:
            await asyncio.wait_for(entered.wait(), timeout=10)
            prepared_id = await prepare_document_draft(snapshot)
        finally:
            release.set()
        with pytest.raises(BadRequestError, match="quota exceeded"):
            await asyncio.wait_for(upload, timeout=10)
        assert (await get_usage(workspace.id)).bytes_used == len(draft_bytes)
        prepared = await postgres_raw_db.get("node", prepared_id)
        assert prepared["context"]["owner_kind"] == "chat"
        visible = await upload_thread.nodes(
            edge=["HAS_ATTACHMENT"], node=["Attachment"], direction="out"
        )
        assert not any(
            isinstance(a, Attachment) and a.scan_status in {"clean", "skipped"}
            for a in visible
        )
        assert len(visible) == 1
        _, _, refusal = await validate_chat_attachment_source(
            user_id=auth.id,
            workspace_id=workspace.id,
            thread_id=upload_thread.id,
            attachment_id=visible[0].id,
        )
        assert refusal["error_code"] == "attachment_not_found"


async def test_upload_retries_after_post_commit_failure_without_double_charging(
    postgres_raw_db, monkeypatch, tmp_path
):
    """A lost response after publication returns the original rooted upload."""
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "22" * 32)
    storage = attachment_storage.AttachmentStorageService(
        create_storage(
            "local",
            root_dir=str(tmp_path),
            allowed_mime_types=attachment_storage._storage_allowed_mime_types(),
            create_root=True,
        )
    )
    monkeypatch.setattr(attachment_storage, "_ATTACHMENT_STORAGE_SERVICE", storage)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, _, _ = await proposal()
        thread = await create_thread(
            user_id=auth.id, provider_id="integral_native", workspace_id=workspace.id
        )
        content = b"A durable uploaded source."
        original_finalize = chat_upload_finalization.finalize_chat_upload

        async def lose_response(**kwargs):
            await original_finalize(**kwargs)
            raise RuntimeError("lost response")

        monkeypatch.setattr(
            chat_upload_finalization, "finalize_chat_upload", lose_response
        )
        metadata = MagicMock()
        monkeypatch.setattr(
            chat_attachment_upload, "get_metadata_extraction_service", lambda: metadata
        )

        async def upload():
            return await chat_attachment_upload.persist_chat_upload(
                thread=thread,
                user_id=auth.id,
                file=UploadFile(
                    file=io.BytesIO(content),
                    filename="upload.txt",
                    headers=Headers({"content-type": "text/plain"}),
                ),
            )

        with pytest.raises(RuntimeError, match="lost response"):
            await upload()
        monkeypatch.setattr(
            chat_upload_finalization, "finalize_chat_upload", original_finalize
        )
        recovered = await upload()
        assert recovered["deduplicated"] is True
        assert (await get_usage(workspace.id)).bytes_used == len(content)
        metadata.schedule_extraction.assert_called_once_with(
            recovered["attachment"]["id"]
        )
        events = await postgres_raw_db.find(
            "object",
            {
                "entity": "OperationEventOutbox",
                "context.event.resource_id": recovered["attachment"]["id"],
            },
        )
        assert len(events) == 1 and events[0]["context"]["status"] == "pending"
        assert (
            await thread.count_nodes(
                edge=["HAS_ATTACHMENT"], node=["Attachment"], direction="out"
            )
            == 1
        )


async def test_upload_audit_failure_rolls_back_visibility_and_charge(
    postgres_raw_db, monkeypatch, tmp_path
):
    """A failed audit write cannot leave a visible, charged upload behind."""
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "22" * 32)
    storage = attachment_storage.AttachmentStorageService(
        create_storage(
            "local",
            root_dir=str(tmp_path),
            allowed_mime_types=attachment_storage._storage_allowed_mime_types(),
            create_root=True,
        )
    )
    monkeypatch.setattr(attachment_storage, "_ATTACHMENT_STORAGE_SERVICE", storage)
    monkeypatch.setattr(
        event_outbox,
        "insert_revision_event",
        AsyncMock(side_effect=RuntimeError("audit unavailable")),
    )
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, _, _ = await proposal()
        thread = await create_thread(
            user_id=auth.id, provider_id="integral_native", workspace_id=workspace.id
        )
        with pytest.raises(RuntimeError, match="audit unavailable"):
            await chat_attachment_upload.persist_chat_upload(
                thread=thread,
                user_id=auth.id,
                file=UploadFile(
                    file=io.BytesIO(b"A source with a failed audit write."),
                    filename="upload.txt",
                    headers=Headers({"content-type": "text/plain"}),
                ),
            )
        assert (await get_usage(workspace.id)).bytes_used == 0
        attachments = await thread.nodes(
            edge=["HAS_ATTACHMENT"], node=["Attachment"], direction="out"
        )
        assert len(attachments) == 1 and attachments[0].scan_status == "pending"
        _, _, refusal = await validate_chat_attachment_source(
            user_id=auth.id,
            workspace_id=workspace.id,
            thread_id=thread.id,
            attachment_id=attachments[0].id,
        )
        assert refusal["error_code"] == "attachment_not_found"
