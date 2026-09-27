"""Chat file attachments (Slice B) — schema, context note, and read contract.

Files uploaded via ``POST /chat/threads/{id}/attachments`` (Task B2) are
referenced by id on the next ``SendMessageRequest.attachment_ids``. The
endpoint resolves ids scoped to the thread, persists a ``file`` part on the
user's ``ChatMessage``, and prepends a context note to the turn's utterance
so the agent knows the file exists and how to read it.
"""

from typing import Any, Dict
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from app.api.attachments import _persist_uploaded_chat_file
from app.schemas.api.ai_chat import SendMessageRequest
from app.services import chat_threads as chat_store

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
# Endpoint — attachment_ids build a context note + persisted file part
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


@pytest.mark.asyncio
async def test_send_message_with_attachment_builds_context_note(
    authenticated_client: AsyncClient, test_user
):
    workspace_id = await _create_workspace(authenticated_client)
    create = await authenticated_client.post(
        "/api/chat/threads",
        headers=_scope_headers(workspace_id),
        json={"provider_id": "jvagent", "agent_id": "aiva"},
    )
    thread_id = create.json()["id"]

    thread = await chat_store.get_thread(thread_id)
    upload = _make_upload(b"hello world", "notes.txt", "text/plain")
    result = await _persist_uploaded_chat_file(
        thread=thread, user_id=test_user.id, file=upload
    )
    attachment_id = result["attachment"]["id"]

    seen: Dict[str, Any] = {}

    async def fake_stream(self, ctx):
        seen["text"] = ctx.text
        if False:
            yield  # async generator, yields nothing

    with patch(
        "app.services.chat_providers.jvagent_provider.JvagentProvider.stream_turn",
        new=fake_stream,
    ):
        resp = await authenticated_client.post(
            f"/api/chat/threads/{thread_id}/messages",
            headers=_scope_headers(workspace_id),
            json={"attachment_ids": [attachment_id]},
        )
        assert resp.status_code == 200, resp.text

    assert "notes.txt" in seen["text"]
    assert attachment_id in seen["text"]
    # The agent must be steered toward the filing tool, not just told how to
    # read the file — otherwise it creates the entry and never calls it
    # (observed regression: entry created, file never attached).
    assert "integral_attach_uploaded_file_to_entry" in seen["text"]
    # And toward the batch/token pattern for a NEW entry — otherwise it
    # passes create_entry's staged_token as entry_id (observed regression:
    # "Entry not found", since the entry doesn't exist until approved).
    assert "staged_token" in seen["text"]
    assert "integral_begin_batch" in seen["text"]
    assert "{{entry.id}}" in seen["text"]

    fetched = await authenticated_client.get(
        f"/api/chat/threads/{thread_id}", headers=_scope_headers(workspace_id)
    )
    messages = fetched.json()["messages"]
    user_message = next(m for m in messages if m["role"] == "user")
    file_parts = [p for p in user_message["parts"] if p.get("type") == "file"]
    assert file_parts and file_parts[0]["attachment_id"] == attachment_id
    assert file_parts[0]["filename"] == "notes.txt"


@pytest.mark.asyncio
async def test_send_message_drops_attachment_id_not_owned_by_thread(
    authenticated_client: AsyncClient, test_user
):
    """An id that isn't attached to this thread is silently ignored, not a 400."""
    workspace_id = await _create_workspace(authenticated_client)
    create = await authenticated_client.post(
        "/api/chat/threads",
        headers=_scope_headers(workspace_id),
        json={"provider_id": "jvagent", "agent_id": "aiva"},
    )
    thread_id = create.json()["id"]

    seen: Dict[str, Any] = {}

    async def fake_stream(self, ctx):
        seen["text"] = ctx.text
        if False:
            yield

    with patch(
        "app.services.chat_providers.jvagent_provider.JvagentProvider.stream_turn",
        new=fake_stream,
    ):
        resp = await authenticated_client.post(
            f"/api/chat/threads/{thread_id}/messages",
            headers=_scope_headers(workspace_id),
            json={"text": "hi", "attachment_ids": ["n.Attachment.doesnotexist"]},
        )
        assert resp.status_code == 200, resp.text

    assert seen["text"].endswith("\n\nhi")
    assert "doesnotexist" not in seen["text"]


def _make_upload(content: bytes, filename: str, content_type: str):
    import io

    from starlette.datastructures import Headers
    from starlette.datastructures import UploadFile as StarletteUploadFile

    return StarletteUploadFile(
        file=io.BytesIO(content),
        filename=filename,
        headers=Headers({"content-type": content_type}),
    )
