"""Chat file attachments (Slice B) — schema, context note, and read contract.

Files uploaded via ``POST /chat/threads/{id}/attachments`` (Task B2) are
referenced by id on the next ``SendMessageRequest.attachment_ids``. The
endpoint resolves ids scoped to the thread, persists a ``file`` part on the
user's ``ChatMessage``, and passes file-handling guidance through the signed
host context so it is not presented as user-authored text.
"""

import asyncio
import hashlib
from typing import Any, Dict
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from app.schemas.api.ai_chat import SendMessageRequest

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_accepts_attachment_only_message():
    req = SendMessageRequest.model_validate({"attachment_ids": ["n.Attachment.abc"]})
    assert req.text == ""
    assert req.attachment_ids == ["n.Attachment.abc"]


def test_rejects_empty_text_images_and_attachments():
    with pytest.raises(ValueError):
        SendMessageRequest.model_validate(
            {"text": "", "images": None, "attachment_ids": None}
        )


def test_rejects_too_many_attachment_ids():
    ids = [f"n.Attachment.{i}" for i in range(11)]
    with pytest.raises(ValueError):
        SendMessageRequest.model_validate({"attachment_ids": ids})


# ---------------------------------------------------------------------------
# Endpoint — attachment_ids use host context + persisted file part
# ---------------------------------------------------------------------------


async def _create_workspace(client: AsyncClient) -> str:
    resp = await client.post("/api/workspaces", json={"name": "Attachments WS"})
    assert resp.status_code == 200, resp.text
    workspace_id = resp.json()["workspace"]["id"]
    scope = await client.put("/api/users/me/scope", json={"workspace_id": workspace_id})
    assert scope.status_code == 200, scope.text
    return workspace_id


def _scope_headers(workspace_id: str) -> Dict[str, str]:
    return {"X-Integral-Scope": f"ws:{workspace_id}"}


pytestmark = pytest.mark.usefixtures("standalone_chat_provider")


@pytest.mark.asyncio
async def test_send_message_with_attachment_builds_context_note(
    authenticated_client: AsyncClient,
):
    workspace_id = await _create_workspace(authenticated_client)
    create = await authenticated_client.post(
        "/api/chat/threads",
        headers=_scope_headers(workspace_id),
        json={"provider_id": "test-provider", "agent_id": "aiva"},
    )
    thread_id = create.json()["id"]

    upload = await authenticated_client.post(
        f"/api/chat/threads/{thread_id}/attachments",
        headers=_scope_headers(workspace_id),
        files={"file": ("notes.txt", b"hello world", "text/plain")},
    )
    assert upload.status_code == 200, upload.text
    attachment_id = upload.json()["attachment"]["id"]

    seen: Dict[str, Any] = {}

    async def fake_stream(self, ctx):
        seen["text"] = ctx.text
        seen["system_context"] = ctx.system_context or ""
        if False:
            yield  # async generator, yields nothing

    with patch(
        "tests.chat_provider_double.StandaloneTestProvider.stream_turn",
        new=fake_stream,
    ):
        resp = await authenticated_client.post(
            f"/api/chat/threads/{thread_id}/messages",
            headers=_scope_headers(workspace_id),
            json={"attachment_ids": [attachment_id]},
        )
        assert resp.status_code == 200, resp.text

    assert seen["text"] == ""
    assert "notes.txt" in seen["system_context"]
    assert attachment_id in seen["system_context"]
    # The agent must be steered toward the filing tool, not just told how to
    # read the file — otherwise it creates the entry and never calls it
    # (observed regression: entry created, file never attached).
    assert "integral_attach_uploaded_file_to_entry" in seen["system_context"]
    # And toward the batch/token pattern for a NEW entry — otherwise it
    # passes create_entry's staged_token as entry_id (observed regression:
    # "Entry not found", since the entry doesn't exist until approved).
    assert "staged_token" in seen["system_context"]
    assert "integral_begin_batch" in seen["system_context"]
    assert "{{entry.id}}" in seen["system_context"]

    fetched = await authenticated_client.get(
        f"/api/chat/threads/{thread_id}", headers=_scope_headers(workspace_id)
    )
    messages = fetched.json()["messages"]
    user_message = next(m for m in messages if m["role"] == "user")
    file_parts = [p for p in user_message["parts"] if p.get("type") == "file"]
    assert file_parts and file_parts[0]["attachment_id"] == attachment_id
    assert file_parts[0]["filename"] == "notes.txt"


@pytest.mark.asyncio
async def test_turn_binds_uploaded_file_after_extraction(
    authenticated_client: AsyncClient, test_user, monkeypatch
):
    from app.schemas.agentive.work import WorkError
    from app.services.attachment_agent import get_attachment_text
    from app.services.attachment_metadata import get_metadata_extraction_service
    from app.services.chat_threads import get_thread, resolve_message_attachments
    from app.services.chat_turn_attachments import (
        assert_chat_attachment_bindings,
        prepare_chat_attachment_bindings,
    )
    from app.services.document_drafts import render_document_draft

    service = get_metadata_extraction_service()
    run_extraction = service._run_extraction
    extract_now = service.extract_now
    allow_extraction = asyncio.Event()

    async def delayed_extraction(attachment_id):
        await allow_extraction.wait()
        return await run_extraction(attachment_id)

    async def join_extraction(attachment_id):
        allow_extraction.set()
        return await extract_now(attachment_id)

    monkeypatch.setattr(service, "_run_extraction", delayed_extraction)
    monkeypatch.setattr(service, "extract_now", join_extraction)
    workspace_id = await _create_workspace(authenticated_client)
    headers = _scope_headers(workspace_id)
    create = await authenticated_client.post(
        "/api/chat/threads",
        headers=headers,
        json={"provider_id": "test-provider", "agent_id": "aiva"},
    )
    assert create.status_code == 200, create.text
    thread_id = create.json()["id"]
    note = "The sample dock opens at 07:40."
    pdf = render_document_draft(title="Dock hours", body=note, format="pdf")
    upload = await authenticated_client.post(
        f"/api/chat/threads/{thread_id}/attachments",
        headers=headers,
        files={"file": ("dock-hours.pdf", pdf.content, "application/pdf")},
    )
    assert upload.status_code == 200, upload.text
    record = upload.json()["attachment"]
    assert record["metadata_status"] == "pending"
    thread = await get_thread(thread_id)
    parts, _ = await resolve_message_attachments(thread, [record["id"]])

    try:
        bindings = await prepare_chat_attachment_bindings(
            thread=thread, principal_id=test_user.user_id, parts=parts
        )
    finally:
        if not allow_extraction.is_set():
            await join_extraction(record["id"])
    result = await get_attachment_text(test_user.user_id, record["id"])
    assert note in result["text"]
    assert (
        bindings[0]["extracted_text_digest"]
        == hashlib.sha256(result["text"].encode()).hexdigest()
    )
    await assert_chat_attachment_bindings(
        thread=thread, principal_id=test_user.user_id, parts=parts, expected=bindings
    )
    bindings[0]["extracted_text_digest"] = "0" * 64
    with pytest.raises(WorkError, match="accepted chat attachment changed"):
        await assert_chat_attachment_bindings(
            thread=thread,
            principal_id=test_user.user_id,
            parts=parts,
            expected=bindings,
        )


@pytest.mark.asyncio
async def test_send_message_drops_attachment_id_not_owned_by_thread(
    authenticated_client: AsyncClient,
):
    """An id that isn't attached to this thread is silently ignored, not a 400."""
    workspace_id = await _create_workspace(authenticated_client)
    create = await authenticated_client.post(
        "/api/chat/threads",
        headers=_scope_headers(workspace_id),
        json={"provider_id": "test-provider", "agent_id": "aiva"},
    )
    thread_id = create.json()["id"]

    seen: Dict[str, Any] = {}

    async def fake_stream(self, ctx):
        seen["text"] = ctx.text
        seen["system_context"] = ctx.system_context or ""
        if False:
            yield

    with patch(
        "tests.chat_provider_double.StandaloneTestProvider.stream_turn",
        new=fake_stream,
    ):
        resp = await authenticated_client.post(
            f"/api/chat/threads/{thread_id}/messages",
            headers=_scope_headers(workspace_id),
            json={"text": "hi", "attachment_ids": ["n.Attachment.doesnotexist"]},
        )
        assert resp.status_code == 200, resp.text

    assert seen["text"] == "hi"
    assert "doesnotexist" not in seen["system_context"]
