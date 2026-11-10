"""Private draft rendering, scoped download and revision checks."""

import io

import pytest

from app.agentive import artifacts
from app.agentive.tooling.dispatch import dispatch_tool
from app.services import chat_threads
from app.services.chat_tool_summary import normalize_tool_summary, tool_summary
from app.services.chat_turn_transcript import _public_parts, fold_chat_transcript
from app.services.document_draft_access import (
    load_document_draft_snapshot,
    require_current_document_draft,
)
from app.services.document_drafts import render_document_draft


def test_draft_receipt_is_bounded_and_survives_public_projection():
    """Only a typed locator reaches chat, never the document body or title."""
    content = {
        "ok": True,
        "_kind": "document_draft",
        "thread_id": "n.ChatThread.abc",
        "key": "report",
        "version": 1,
        "format": "docx",
        "title": "Private title",
        "body": "Private content",
    }
    projected = tool_summary("integral_upsert_artifact", content, failed=False)
    assert normalize_tool_summary(projected)["document_draft"] == {
        "thread_id": "n.ChatThread.abc",
        "key": "report",
        "version": 1,
        "format": "docx",
    }
    assert "Private" not in str(projected)
    parts, _ = fold_chat_transcript(
        [
            {
                "type": "tool-call",
                "toolCallId": "call-1",
                "name": "integral_upsert_artifact",
                "status": "complete",
                "summary": projected,
            }
        ]
    )
    assert _public_parts(parts)[0]["summary"] == projected
    assert "document_draft" not in tool_summary("other_tool", content, failed=False)
    assert "document_draft" not in tool_summary(
        "integral_upsert_artifact", {**content, "key": "../bad"}, failed=False
    )


def test_cleanup_refuses_storage_key_outside_the_attempt():
    """A corrupted ledger cannot turn cleanup into an arbitrary blob delete."""
    from app.services.document_draft_cleanup import _safe_attempt_key

    attempt = {
        "id": "o.HarnessDraftBlobAttempt." + "a" * 32,
        "context": {
            "thread_id": "n.ChatThread.test",
            "storage_key": "attachments/another-user/important.pdf",
        },
    }
    assert _safe_attempt_key(attempt) == ""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", [{"body": "changed"}, {"metadata": {"format": "pdf"}}]
)
async def test_snapshot_rejects_content_changes_even_when_version_is_unchanged(
    monkeypatch, change
):
    """The source digest binds the reviewed content as well as its revision."""
    from app.api.errors import ResourceConflictError

    draft = {
        "ok": True,
        "kind": "document_draft",
        "version": 1,
        "title": "Private title",
        "body": "Private body",
        "metadata": {"format": "docx"},
    }

    async def read(**_kwargs):
        return dict(draft)

    monkeypatch.setattr(artifacts, "get_artifact", read)
    snapshot = await load_document_draft_snapshot(
        user_id="u", workspace_id="w", thread_id="t", key="k", version=1
    )
    assert "Private" not in repr(snapshot)
    draft.update(change)
    with pytest.raises(ResourceConflictError):
        await require_current_document_draft(snapshot)


@pytest.mark.parametrize("format", ["markdown", "docx", "pdf", "pptx"])
def test_rendered_draft_is_readable(format):
    """Each supported format is reopened by its real parser."""
    rendered = render_document_draft(
        title="Research", body="# Evidence\n\nA source-backed point.", format=format
    )
    assert rendered.content
    assert rendered.units > 0
    if format == "markdown":
        assert "source-backed" in rendered.content.decode("utf-8")
    elif format == "docx":
        from docx import Document

        assert "source-backed" in " ".join(
            p.text for p in Document(io.BytesIO(rendered.content)).paragraphs
        )
    elif format == "pdf":
        from pypdf import PdfReader

        assert "source-backed" in " ".join(
            page.extract_text()
            for page in PdfReader(io.BytesIO(rendered.content)).pages
        )
    else:
        from pptx import Presentation

        slides = Presentation(io.BytesIO(rendered.content)).slides
        assert any(
            "source-backed" in shape.text
            for slide in slides
            for shape in slide.shapes
            if shape.has_text_frame
        )


def test_pdf_rejects_silent_unicode_corruption_and_suggests_alternatives():
    """A structurally valid PDF must not silently replace source characters."""
    body = "# Welcome\n\nHello 漢字 العربية"
    with pytest.raises(ValueError, match="Choose Word or Markdown"):
        render_document_draft(title="Guide", body=body, format="pdf")
    from docx import Document

    word = render_document_draft(title="Guide", body=body, format="docx")
    assert "漢字 العربية" in " ".join(
        paragraph.text for paragraph in Document(io.BytesIO(word.content)).paragraphs
    )


@pytest.mark.parametrize("format", ["docx", "markdown"])
def test_export_does_not_duplicate_a_matching_title(format):
    """A repeated leading Markdown title appears once in the actual file."""
    rendered = render_document_draft(
        title="Guide", body="# Guide\n\nWelcome.", format=format
    )
    if format == "markdown":
        assert rendered.content.decode("utf-8").count("# Guide") == 1
    else:
        from docx import Document

        paragraphs = [p.text for p in Document(io.BytesIO(rendered.content)).paragraphs]
        assert paragraphs.count("Guide") == 1
        assert "Welcome." in paragraphs


def test_identical_draft_renders_have_stable_bytes():
    """Retries retain exact bytes, not a new timestamp-dependent file."""
    import time

    formats = ("markdown", "docx", "pdf", "pptx")

    def render(format):
        return render_document_draft(
            title="Guide", body="# Guide\n\nA source-backed point.", format=format
        ).content

    first = {format: render(format) for format in formats}
    # Office ZIP timestamps have a two-second granularity, so cross that
    # boundary; without canonicalization this assertion fails for real files.
    time.sleep(2.1)
    assert {format: render(format) for format in formats} == first


def test_pdf_preserves_common_punctuation_and_quoted_link_text():
    """Normal international punctuation and link attributes render safely."""
    from pypdf import PdfReader

    body = 'Café costs €12 — see [report](https://example.com/a"quoted).'
    rendered = render_document_draft(title="Guide", body=body, format="pdf")
    pdf = PdfReader(io.BytesIO(rendered.content))
    text = " ".join(page.extract_text() for page in pdf.pages)
    assert "Café costs €12 — see" in text
    assert "report" in text
    link = pdf.pages[0]["/Annots"][0].get_object()["/A"]["/URI"]
    assert link == 'https://example.com/a"quoted'


@pytest.mark.asyncio
async def test_private_draft_download_and_revision_fence(
    authenticated_client, test_user, second_user_client
):
    """Downloads bind to owner, workspace, draft kind and exact revision."""
    response = await authenticated_client.post(
        "/api/workspaces", json={"name": "Draft test"}
    )
    assert response.status_code == 200
    workspace = response.json()["workspace"]["id"]
    thread = await chat_threads.create_thread(
        user_id=test_user.user_id, provider_id="integral_native", workspace_id=workspace
    )
    args = {
        "key": "report",
        "kind": "document_draft",
        "title": "Research report",
        "body": "# Evidence\n\nVerified source.",
        "metadata": {"format": "docx"},
        "expected_version": 0,
    }
    result = await dispatch_tool(
        "integral_upsert_artifact",
        args,
        principal_id=test_user.user_id,
        scope=workspace,
        session_id=thread.id,
    )
    assert not result.is_error, result
    assert result.data["_kind"] == "document_draft"
    assert result.data["thread_id"] == thread.id and result.data["version"] == 1
    unfenced = await artifacts.upsert_artifact(
        user_id=test_user.user_id,
        workspace_id=workspace,
        session_id=thread.id,
        key="report",
        kind="note",
        body="An unfenced replacement",
    )
    assert unfenced["error"] == "version_required"
    stale_replacement = await artifacts.upsert_artifact(
        user_id=test_user.user_id,
        workspace_id=workspace,
        session_id=thread.id,
        key="report",
        kind="note",
        body="A stale replacement",
        expected_version=2,
    )
    assert stale_replacement["error"] == "version_conflict"
    unchanged = await artifacts.get_artifact(
        user_id=test_user.user_id,
        workspace_id=workspace,
        session_id=thread.id,
        key="report",
    )
    assert unchanged["kind"] == "document_draft"
    assert unchanged["version"] == 1
    snapshot = await load_document_draft_snapshot(
        user_id=test_user.user_id,
        workspace_id=workspace,
        thread_id=thread.id,
        key="report",
        version=1,
    )
    assert len(snapshot.source_digest) == 64
    assert "Verified source" not in repr(snapshot)
    await require_current_document_draft(snapshot)
    path = f"/api/chat/threads/{thread.id}/artifacts/report/download?version=1"
    preview_path = path.replace("/download?", "/preview?")
    headers = {"X-Integral-Scope": f"ws:{workspace}"}
    preview = await authenticated_client.get(preview_path, headers=headers)
    assert preview.status_code == 200
    assert preview.json()["body"] == "# Evidence\n\nVerified source."
    assert preview.headers["cache-control"] == "private, no-store"
    assert (
        await second_user_client.get(preview_path, headers=headers)
    ).status_code in {403, 404}
    downloaded = await authenticated_client.get(path, headers=headers)
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.headers["content-type"].startswith(
        "application/vnd.openxmlformats"
    )
    from docx import Document

    doc = Document(io.BytesIO(downloaded.content))
    assert "Verified source." in " ".join(p.text for p in doc.paragraphs)
    denied = await second_user_client.get(path, headers=headers)
    assert denied.status_code in {403, 404}
    revised = await artifacts.upsert_artifact(
        user_id=test_user.user_id,
        workspace_id=workspace,
        session_id=thread.id,
        key="report",
        kind="document_draft",
        title="Research report",
        body="Revised.",
        metadata={"format": "docx"},
        expected_version=1,
    )
    assert revised["version"] == 2
    from app.api.errors import ResourceConflictError

    with pytest.raises(ResourceConflictError):
        await require_current_document_draft(snapshot)
    assert (await authenticated_client.get(path, headers=headers)).status_code == 409
    assert (
        await authenticated_client.get(preview_path, headers=headers)
    ).status_code == 409
    current = await authenticated_client.get(
        path.replace("version=1", "version=2"), headers=headers
    )
    assert current.status_code == 200
    assert "Revised." in " ".join(
        p.text for p in Document(io.BytesIO(current.content)).paragraphs
    )


@pytest.mark.asyncio
async def test_document_draft_rejects_missing_version_or_unsupported_format(
    monkeypatch,
):
    """A draft requires an explicit revision fence and a supported format."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    monkeypatch.setattr(
        artifacts,
        "_resolve_artifact_thread",
        AsyncMock(return_value=SimpleNamespace(id="t")),
    )
    missing = await artifacts.upsert_artifact(
        user_id="u",
        session_id="s",
        key="report",
        kind="document_draft",
        body="text",
        metadata={"format": "docx"},
    )
    assert missing["error"] == "version_required"
    unsupported = await artifacts.upsert_artifact(
        user_id="u",
        session_id="s",
        key="report",
        kind="document_draft",
        body="text",
        metadata={"format": "xlsx"},
        expected_version=0,
    )
    assert unsupported["error"] == "draft_render_failed"


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_preparation_claim_recovery_and_exact_attachment(
    authenticated_client, test_user, second_user, monkeypatch
):
    """Only a current claim may bind the exact authorized draft attachment."""
    import asyncio
    import hashlib
    import time
    from dataclasses import replace
    from unittest.mock import AsyncMock

    from jvspatial.core.context import get_default_context
    from starlette.datastructures import Headers, UploadFile

    from app.api.errors import ResourceConflictError, ResourceNotFoundError
    from app.services.attachment_storage import get_attachment_storage_service
    from app.services.chat_attachment_upload import persist_chat_upload
    from app.services.document_draft_preparation import (
        DraftPreparation,
        claim_draft_preparation,
        finish_draft_preparation,
        stage_draft_preparation_blob,
    )

    response = await authenticated_client.post(
        "/api/workspaces", json={"name": "Preparation test"}
    )
    workspace = response.json()["workspace"]["id"]
    user_id = test_user.user_id
    thread = await chat_threads.create_thread(
        user_id=user_id, provider_id="integral_native", workspace_id=workspace
    )
    saved = await artifacts.upsert_artifact(
        user_id=user_id,
        workspace_id=workspace,
        session_id=thread.id,
        key="report",
        kind="document_draft",
        title="Report",
        body="Original",
        metadata={"format": "markdown"},
        expected_version=0,
    )
    assert saved["version"] == 1
    snapshot = await load_document_draft_snapshot(
        user_id=user_id,
        workspace_id=workspace,
        thread_id=thread.id,
        key="report",
        version=1,
    )
    content = render_document_draft(
        title=snapshot.title, body=snapshot.body, format=snapshot.format
    ).content
    digest = hashlib.sha256(content).hexdigest()
    with pytest.raises(ResourceConflictError):
        await require_current_document_draft(replace(snapshot, body="Forged body"))
    with pytest.raises(ResourceNotFoundError):
        await claim_draft_preparation(
            replace(snapshot, user_id=second_user.user_id),
            binary_digest=digest,
            binary_size=len(content),
        )
    with pytest.raises(ResourceConflictError):
        await claim_draft_preparation(
            snapshot, binary_digest="a" * 64, binary_size=len(content)
        )
    competing = await asyncio.gather(
        claim_draft_preparation(
            snapshot, binary_digest=digest, binary_size=len(content)
        ),
        claim_draft_preparation(
            snapshot, binary_digest=digest, binary_size=len(content)
        ),
        return_exceptions=True,
    )
    winners = [result for result in competing if isinstance(result, DraftPreparation)]
    losers = [
        result for result in competing if isinstance(result, ResourceConflictError)
    ]
    assert len(winners) == len(losers) == 1
    first = winners[0]
    await get_default_context().database.find_one_and_update(
        "object",
        {"id": first.record_id},
        {"$set": {"_jv_claim_until": time.time() - 1}},
    )
    recovered = await claim_draft_preparation(
        snapshot, binary_digest=digest, binary_size=len(content)
    )
    assert recovered.token != first.token
    with pytest.raises(ResourceConflictError):
        await stage_draft_preparation_blob(snapshot, first)
    first_blob = await stage_draft_preparation_blob(snapshot, recovered)
    second_blob = await stage_draft_preparation_blob(snapshot, recovered)
    assert first_blob.storage_key != second_blob.storage_key
    storage = get_attachment_storage_service()
    assert await storage.read_attachment(first_blob.storage_key) == content
    row = await get_default_context().database.get("object", first_blob.attempt_id)
    assert row["context"]["storage_key"] == first_blob.storage_key
    assert row["context"]["preparation_id"] == recovered.record_id

    # Simulate the lease expiring while the old worker is blocked in blob I/O.
    # Its attempt remains discoverable but cannot be accepted after displacement.
    original_save = storage.save_attachment
    displaced_key = None

    async def save_after_expiry(**kwargs):
        nonlocal displaced_key
        # The recovery ledger must be durable before the external write.
        registered = await get_default_context().database.get(
            "object", kwargs["metadata"]["attempt_id"]
        )
        assert registered is not None
        assert registered["context"]["storage_key"] == storage.build_storage_key(
            kwargs["entry_id"], kwargs["attachment_id"], kwargs["filename"]
        )
        result = await original_save(**kwargs)
        displaced_key = result["path"]
        await get_default_context().database.find_one_and_update(
            "object",
            {"id": recovered.record_id},
            {"$set": {"_jv_claim_until": time.time() - 1}},
        )
        return result

    monkeypatch.setattr(storage, "save_attachment", save_after_expiry)
    with pytest.raises(ResourceConflictError, match="expired or changed"):
        await stage_draft_preparation_blob(snapshot, recovered)
    monkeypatch.setattr(storage, "save_attachment", original_save)
    recovered = await claim_draft_preparation(
        snapshot, binary_digest=digest, binary_size=len(content)
    )
    winner_blob = await stage_draft_preparation_blob(snapshot, recovered)
    assert displaced_key and winner_blob.storage_key != displaced_key
    assert await storage.read_attachment(displaced_key) == content

    async def upload(value):
        return await persist_chat_upload(
            thread=thread,
            user_id=user_id,
            file=UploadFile(
                file=io.BytesIO(value),
                filename="report.md",
                headers=Headers({"content-type": "text/markdown"}),
            ),
        )

    wrong = await upload(b"not the reviewed document")
    with pytest.raises(ResourceNotFoundError):
        await finish_draft_preparation(
            snapshot, recovered, attachment_id=wrong["attachment"]["id"]
        )
    right = await upload(content)
    attachment_id = right["attachment"]["id"]
    read_original = storage.read_attachment
    monkeypatch.setattr(
        storage, "read_attachment", AsyncMock(return_value=b"changed bytes")
    )
    with pytest.raises(ResourceConflictError, match="bytes changed"):
        await finish_draft_preparation(snapshot, recovered, attachment_id=attachment_id)
    monkeypatch.setattr(storage, "read_attachment", read_original)
    with pytest.raises(ResourceConflictError):
        await finish_draft_preparation(snapshot, first, attachment_id=attachment_id)
    await finish_draft_preparation(snapshot, recovered, attachment_id=attachment_id)
    replay = await claim_draft_preparation(
        snapshot, binary_digest=digest, binary_size=len(content)
    )
    assert replay.attachment_id == attachment_id and not replay.token
    monkeypatch.setattr(
        storage, "read_attachment", AsyncMock(return_value=b"changed bytes")
    )
    with pytest.raises(ResourceConflictError, match="bytes changed"):
        await claim_draft_preparation(
            snapshot, binary_digest=digest, binary_size=len(content)
        )
    monkeypatch.setattr(storage, "read_attachment", read_original)

    await artifacts.upsert_artifact(
        user_id=user_id,
        workspace_id=workspace,
        session_id=thread.id,
        key="report",
        kind="document_draft",
        title="Report",
        body="Changed",
        metadata={"format": "markdown"},
        expected_version=1,
    )
    with pytest.raises(ResourceConflictError):
        await claim_draft_preparation(
            snapshot, binary_digest=digest, binary_size=len(content)
        )


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_staged_draft_publication_is_fenced_and_charged_once(
    authenticated_client, second_user_client, test_user, monkeypatch, tmp_path
):
    """One reviewed draft produces one rooted attachment and one usage charge."""
    import asyncio
    import hashlib
    import json
    import os
    import sys
    from unittest.mock import AsyncMock

    from jvspatial.core.context import get_default_context
    from jvspatial.storage import create_storage

    from app.api.errors import BadRequestError, ResourceConflictError
    from app.config import settings
    from app.models.edges import HAS_ATTACHMENT
    from app.models.nodes import Attachment, ChatThread
    from app.services import attachment_storage
    from app.services.attachment_storage import get_attachment_storage_service
    from app.services.document_draft_preparation import (
        claim_draft_preparation,
        publish_staged_draft,
        stage_draft_preparation_blob,
    )

    isolated_storage = attachment_storage.AttachmentStorageService(
        create_storage(
            "local",
            root_dir=str(tmp_path),
            allowed_mime_types=attachment_storage._storage_allowed_mime_types(),
            create_root=True,
        )
    )
    monkeypatch.setattr(
        attachment_storage, "_ATTACHMENT_STORAGE_SERVICE", isolated_storage
    )
    response = await authenticated_client.post(
        "/api/workspaces", json={"name": "Draft publication test"}
    )
    workspace = response.json()["workspace"]["id"]
    thread = await chat_threads.create_thread(
        user_id=test_user.user_id,
        provider_id="integral_native",
        workspace_id=workspace,
    )
    await artifacts.upsert_artifact(
        user_id=test_user.user_id,
        workspace_id=workspace,
        session_id=thread.id,
        key="report",
        kind="document_draft",
        title="Report",
        body="Reviewed content",
        metadata={"format": "markdown"},
        expected_version=0,
    )
    snapshot = await load_document_draft_snapshot(
        user_id=test_user.user_id,
        workspace_id=workspace,
        thread_id=thread.id,
        key="report",
        version=1,
    )
    content = render_document_draft(
        title=snapshot.title, body=snapshot.body, format=snapshot.format
    ).content
    digest = hashlib.sha256(content).hexdigest()
    claim = await claim_draft_preparation(
        snapshot, binary_digest=digest, binary_size=len(content)
    )
    attempt = await stage_draft_preparation_blob(snapshot, claim)
    database = get_default_context().database
    storage = get_attachment_storage_service()
    from app.services.document_draft_cleanup import reconcile_draft_blob_attempts

    assert await reconcile_draft_blob_attempts() == 0
    assert await storage.read_attachment(attempt.storage_key) == content
    original_read = storage.read_attachment
    monkeypatch.setattr(storage, "read_attachment", AsyncMock(return_value=b"tampered"))
    with pytest.raises(ResourceConflictError, match="bytes changed"):
        await publish_staged_draft(snapshot, claim, attempt_id=attempt.attempt_id)
    monkeypatch.setattr(storage, "read_attachment", original_read)
    monkeypatch.setattr(settings, "STORAGE_QUOTA_ENFORCE", True)
    await database.find_one_and_update(
        "node",
        {"id": workspace},
        {
            "$set": {
                "context.storage_bytes_used": 0,
                "context.storage_quota_bytes": len(content) - 1,
            }
        },
    )
    with pytest.raises(BadRequestError, match="quota exceeded"):
        await publish_staged_draft(snapshot, claim, attempt_id=attempt.attempt_id)
    assert await thread.count_nodes(edge=[HAS_ATTACHMENT], direction="out") == 0
    await database.find_one_and_update(
        "node",
        {"id": workspace},
        {"$set": {"context.storage_quota_bytes": len(content)}},
    )
    original_connect = ChatThread.connect

    async def fail_connect(*args, **kwargs):
        raise RuntimeError("simulated edge failure")

    before = await database.find(
        "node", {"entity": Attachment._entity_name(), "context.content_hash": digest}
    )
    monkeypatch.setattr(ChatThread, "connect", fail_connect)
    with pytest.raises(RuntimeError, match="simulated edge failure"):
        await publish_staged_draft(snapshot, claim, attempt_id=attempt.attempt_id)
    monkeypatch.setattr(ChatThread, "connect", original_connect)
    assert (
        await database.find(
            "node",
            {"entity": Attachment._entity_name(), "context.content_hash": digest},
        )
        == before
    )
    assert await thread.count_nodes(edge=[HAS_ATTACHMENT], direction="out") == 0
    assert (await database.get("node", workspace))["context"]["storage_bytes_used"] == 0
    assert (await database.get("object", attempt.attempt_id))["context"][
        "state"
    ] == "reserved"
    assert (await database.get("object", claim.record_id))["context"][
        "state"
    ] == "pending"
    results = await asyncio.gather(
        publish_staged_draft(snapshot, claim, attempt_id=attempt.attempt_id),
        publish_staged_draft(snapshot, claim, attempt_id=attempt.attempt_id),
        return_exceptions=True,
    )
    ids = [result for result in results if isinstance(result, str)]
    assert len(ids) == 1
    assert (
        len(
            [
                result
                for result in results
                if isinstance(result, (ResourceConflictError, BadRequestError))
            ]
        )
        == 1
    )
    stored = await database.get("node", ids[0])
    assert stored["entity"] == Attachment._entity_name()
    assert stored["context"]["storage_key"] == attempt.storage_key
    assert stored["context"]["content_hash"] == digest
    assert await thread.count_nodes(edge=[HAS_ATTACHMENT], direction="out") == 1
    assert (await database.get("node", workspace))["context"][
        "storage_bytes_used"
    ] == len(content)
    attempt_row = await database.get("object", attempt.attempt_id)
    assert attempt_row["context"]["state"] == "published"
    assert attempt_row["context"]["attachment_id"] == ids[0]
    replay = await claim_draft_preparation(
        snapshot, binary_digest=digest, binary_size=len(content)
    )
    assert replay.attachment_id == ids[0]

    review_path = (
        f"/api/chat/threads/{thread.id}/attachments/{ids[0]}/review"
        f"?expected_sha256={digest}"
    )
    headers = {"X-Integral-Scope": f"ws:{workspace}"}
    review_response = await authenticated_client.get(review_path, headers=headers)
    assert review_response.status_code == 200, review_response.text
    assert review_response.content == content
    assert review_response.headers["cache-control"] == "private, no-store"
    assert (
        await authenticated_client.get(
            review_path.replace(digest, "f" * 64), headers=headers
        )
    ).status_code == 409
    assert (await second_user_client.get(review_path, headers=headers)).status_code in {
        403,
        404,
    }
    monkeypatch.setattr(
        storage, "read_attachment", AsyncMock(return_value=b"corrupted")
    )
    assert (
        await authenticated_client.get(review_path, headers=headers)
    ).status_code == 409
    monkeypatch.setattr(storage, "read_attachment", original_read)

    async def archive_during_read(key):
        blob = await original_read(key)
        await database.find_one_and_update(
            "node", {"id": thread.id}, {"$set": {"context.archived": True}}
        )
        return blob

    monkeypatch.setattr(storage, "read_attachment", archive_during_read)
    assert (
        await authenticated_client.get(review_path, headers=headers)
    ).status_code in {
        403,
        404,
    }
    monkeypatch.setattr(storage, "read_attachment", original_read)
    await database.find_one_and_update(
        "node", {"id": thread.id}, {"$set": {"context.archived": False}}
    )

    worker = """
import asyncio, json, os
from jvspatial.core.context import GraphContext, set_default_context
from jvspatial.db import get_database_manager
from jvspatial.db.factory import create_database
from app.services.document_draft_access import load_document_draft_snapshot
from app.services.document_draft_preparation import prepare_document_draft

async def run():
    database = create_database(db_type="postgres")
    get_database_manager().set_prime_database(database)
    set_default_context(GraphContext(database=database))
    snapshot = await load_document_draft_snapshot(
        user_id=os.environ["DRAFT_TEST_USER"],
        workspace_id=os.environ["DRAFT_TEST_WORKSPACE"],
        thread_id=os.environ["DRAFT_TEST_THREAD"], key="report", version=1,
    )
    attachment_id = await prepare_document_draft(snapshot)
    print(json.dumps({"pid": os.getpid(), "attachment_id": attachment_id}), flush=True)
    await database.close()

asyncio.run(run())
"""
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        worker,
        env={
            **os.environ,
            "DRAFT_TEST_USER": test_user.user_id,
            "DRAFT_TEST_WORKSPACE": workspace,
            "DRAFT_TEST_THREAD": thread.id,
            "JVSPATIAL_FILES_ROOT_PATH": str(tmp_path),
            "JVSPATIAL_FILE_STORAGE_PROVIDER": "local",
            "EMBEDDING_STORE_DRIVER": "null",
        },
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    assert proc.returncode == 0, stderr.decode()
    fresh = json.loads(stdout.decode().strip().splitlines()[-1])
    assert fresh == {"pid": proc.pid, "attachment_id": ids[0]}
    assert await thread.count_nodes(edge=[HAS_ATTACHMENT], direction="out") == 1
    assert (await database.get("node", workspace))["context"][
        "storage_bytes_used"
    ] == len(content)

    # The generated chat attachment is a valid source for the existing
    # reviewed filing workflow; staging itself must not file it.
    from app.agentive import staging
    from app.agentive.tooling import bindings
    from app.services.agent_scope import current_scope_workspace_id

    monkeypatch.setattr(
        staging,
        "pending_entry_creation",
        AsyncMock(return_value={"kind": "create_entry"}),
    )
    monkeypatch.setattr(
        chat_threads, "get_thread_by_session", AsyncMock(return_value=thread)
    )
    principal_token = bindings._propose_principal.set(test_user.user_id)
    session_token = bindings._propose_session_id.set(thread.id)
    workspace_token = current_scope_workspace_id.set(workspace)
    try:
        proposal = await bindings._stage_attach_uploaded_file(
            {"entry_id": "{{step_1.id}}", "attachment_id": ids[0]}
        )
    finally:
        current_scope_workspace_id.reset(workspace_token)
        bindings._propose_session_id.reset(session_token)
        bindings._propose_principal.reset(principal_token)
    assert proposal["kind"] == "attach_uploaded_file"
    assert proposal["payload"]["attachment_id"] == ids[0]
    assert proposal["diff_machine"]["file_review"] == {
        "thread_id": thread.id,
        "attachment_id": ids[0],
        "filename": "draft.md",
    }
    assert (await database.get("node", ids[0]))["context"]["owner_kind"] == "chat"

    # A worker can commit publication and die before returning the ID. The
    # next invocation must recover the receipt without creating another file.
    from app.services import document_draft_preparation as preparation

    assert await preparation.prepare_document_draft(snapshot) == ids[0]
    await database.find_one_and_update(
        "node", {"id": workspace}, {"$set": {"context.storage_quota_bytes": 0}}
    )
    await artifacts.upsert_artifact(
        user_id=test_user.user_id,
        workspace_id=workspace,
        session_id=thread.id,
        key="second-report",
        kind="document_draft",
        title="Second report",
        body="A distinct revision",
        metadata={"format": "markdown"},
        expected_version=0,
    )
    second = await load_document_draft_snapshot(
        user_id=test_user.user_id,
        workspace_id=workspace,
        thread_id=thread.id,
        key="second-report",
        version=1,
    )
    second_content = render_document_draft(
        title=second.title, body=second.body, format=second.format
    ).content
    stage = preparation.stage_draft_preparation_blob
    monkeypatch.setattr(
        preparation,
        "stage_draft_preparation_blob",
        AsyncMock(side_effect=RuntimeError("temporary storage failure")),
    )
    with pytest.raises(RuntimeError, match="temporary storage failure"):
        await preparation.prepare_document_draft(second)
    monkeypatch.setattr(preparation, "stage_draft_preparation_blob", stage)
    publish = preparation.publish_staged_draft
    committed = []

    async def lose_response(*args, **kwargs):
        committed.append(await publish(*args, **kwargs))
        raise RuntimeError("simulated lost response")

    monkeypatch.setattr(preparation, "publish_staged_draft", lose_response)
    with pytest.raises(RuntimeError, match="simulated lost response"):
        await preparation.prepare_document_draft(second)
    monkeypatch.setattr(preparation, "publish_staged_draft", publish)
    assert await preparation.prepare_document_draft(second) == committed[0]
    assert await thread.count_nodes(edge=[HAS_ATTACHMENT], direction="out") == 2
    assert (await database.get("node", workspace))["context"]["storage_bytes_used"] == (
        len(content) + len(second_content)
    )

    # A separate worker dies after its blob write but before publication.
    # After the native lease expires, the parent must prepare a new attempt.
    await artifacts.upsert_artifact(
        user_id=test_user.user_id,
        workspace_id=workspace,
        session_id=thread.id,
        key="interrupted-report",
        kind="document_draft",
        title="Interrupted report",
        body="Recover after worker death",
        metadata={"format": "markdown"},
        expected_version=0,
    )
    third = await load_document_draft_snapshot(
        user_id=test_user.user_id,
        workspace_id=workspace,
        thread_id=thread.id,
        key="interrupted-report",
        version=1,
    )
    crash_worker = """
import asyncio, hashlib, json, os
from jvspatial.core.context import GraphContext, set_default_context
from jvspatial.db import get_database_manager
from jvspatial.db.factory import create_database
from app.services.document_draft_access import load_document_draft_snapshot
from app.services.document_draft_preparation import claim_draft_preparation, stage_draft_preparation_blob
from app.services.document_drafts import render_document_draft

async def run():
    database = create_database(db_type="postgres")
    get_database_manager().set_prime_database(database)
    set_default_context(GraphContext(database=database))
    snapshot = await load_document_draft_snapshot(
        user_id=os.environ["DRAFT_TEST_USER"], workspace_id=os.environ["DRAFT_TEST_WORKSPACE"],
        thread_id=os.environ["DRAFT_TEST_THREAD"], key="interrupted-report", version=1,
    )
    content = render_document_draft(title=snapshot.title, body=snapshot.body, format=snapshot.format).content
    claim = await claim_draft_preparation(snapshot, binary_digest=hashlib.sha256(content).hexdigest(),
                                           binary_size=len(content), lease_seconds=1)
    attempt = await stage_draft_preparation_blob(snapshot, claim)
    print(json.dumps({"attempt_id": attempt.attempt_id, "storage_key": attempt.storage_key}), flush=True)
    os._exit(0)

asyncio.run(run())
"""
    crash = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        crash_worker,
        env={
            **os.environ,
            "DRAFT_TEST_USER": test_user.user_id,
            "DRAFT_TEST_WORKSPACE": workspace,
            "DRAFT_TEST_THREAD": thread.id,
            "JVSPATIAL_FILES_ROOT_PATH": str(tmp_path),
            "JVSPATIAL_FILE_STORAGE_PROVIDER": "local",
            "EMBEDDING_STORE_DRIVER": "null",
        },
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(crash.communicate(), timeout=30)
    assert crash.returncode == 0, stderr.decode()
    abandoned = json.loads(stdout.decode().strip().splitlines()[-1])
    deadline = asyncio.get_running_loop().time() + 8
    recovered_id = None
    while asyncio.get_running_loop().time() < deadline:
        try:
            recovered_id = await preparation.prepare_document_draft(third)
            break
        except ResourceConflictError as exc:
            assert "still being prepared" in str(exc)
            await asyncio.sleep(0.1)
    assert recovered_id
    recovered = await database.get("node", recovered_id)
    assert recovered["context"]["storage_key"] != abandoned["storage_key"]
    abandoned_row = await database.get("object", abandoned["attempt_id"])
    assert abandoned_row["context"]["state"] == "reserved"
    assert await storage.read_attachment(abandoned["storage_key"]) is not None
    assert await thread.count_nodes(edge=[HAS_ATTACHMENT], direction="out") == 3
    assert sorted(
        await asyncio.gather(
            reconcile_draft_blob_attempts(), reconcile_draft_blob_attempts()
        )
    ) == [0, 1]
    assert await storage.read_attachment(abandoned["storage_key"]) is None
    assert (
        await storage.read_attachment(recovered["context"]["storage_key"]) is not None
    )
    assert (await database.get("object", abandoned["attempt_id"]))["context"][
        "state"
    ] == "abandoned"

    # A displaced storage operation can finish after the first deletion.
    # The retained tombstone must remove that same attempt on a later pass.
    late_content = render_document_draft(
        title=third.title, body=third.body, format=third.format
    ).content
    await storage.save_attachment(
        entry_id=f"draft-preparation/{thread.id}",
        attachment_id=abandoned["attempt_id"].rsplit(".", 1)[-1],
        filename="draft.md",
        content=late_content,
        mime_type="text/markdown",
    )
    await database.find_one_and_update(
        "object",
        {"id": abandoned["attempt_id"]},
        {"$set": {"context.next_cleanup_at": 0}},
    )
    original_delete = storage.delete_attachment
    monkeypatch.setattr(
        storage,
        "delete_attachment",
        AsyncMock(side_effect=OSError("storage unavailable")),
    )
    assert await reconcile_draft_blob_attempts() == 0
    assert await storage.read_attachment(abandoned["storage_key"]) == late_content
    monkeypatch.setattr(storage, "delete_attachment", original_delete)
    await database.find_one_and_update(
        "object",
        {"id": abandoned["attempt_id"]},
        {"$set": {"context.next_cleanup_at": 0}},
    )
    assert await reconcile_draft_blob_attempts() == 1
    assert await storage.read_attachment(abandoned["storage_key"]) is None
    assert (
        await storage.read_attachment(recovered["context"]["storage_key"]) is not None
    )
