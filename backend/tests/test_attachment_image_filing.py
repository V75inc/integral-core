"""On-demand image attach: file a composer-uploaded image onto an entry.

Composer images are retained as base64 on the chat message (vision-only) and
are NOT written to attachment storage on upload. `integral_attach_uploaded_
image_to_entry` materializes the image bytes into an Attachment and wires it to
the target entry only when the user asks — so storage is consumed on the
approved attach. Regression for the "attach image to entry says done but no
attachment" bug (images were never persisted, so there was nothing to attach).
"""

from __future__ import annotations

import base64

import pytest

from app.agentive.tooling import bindings
from app.models.edges import CONTAINS, HAS_ATTACHMENT
from app.models.nodes import Entry, Track
from app.services import chat_threads as chat_store
from app.services.attachment_agent import attach_image_bytes_to_entry
from app.services.chat_threads import find_uploaded_image

# A real 1x1 transparent PNG — the attachment-storage validator sniffs the
# MIME from the content bytes, so the fixture must be a genuine PNG (chat
# images arriving in production are real PNG/JPEG bytes).
_IMG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
_IMG_BYTES = base64.b64decode(_IMG_B64)


class _Allow:
    allowed = True


def _async(value):
    async def _inner(**kwargs):
        return value

    return _inner


async def _seed_entry():
    track = await Track.create(title="Files", visibility="private")
    entry = await Entry.create(title="Target entry", track_id=track.id)
    await track.connect(entry, edge=CONTAINS)
    return entry


async def _seed_thread_with_image(user_id: str, image_id: str = "img1"):
    thread = await chat_store.create_thread(
        user_id=user_id, provider_id="jvagent", workspace_id="ws1"
    )
    thread.provider_session_id = "sess-img"
    await thread.save()
    await chat_store.append_message(
        thread=thread,
        role="user",
        parts=[
            {"type": "text", "text": "file this as a post"},
            {
                "type": "image",
                "content_type": "image/png",
                "image_id": image_id,
                "data": _IMG_B64,
            },
        ],
    )
    return thread


@pytest.mark.asyncio
async def test_find_uploaded_image_by_id_and_recent():
    thread = await _seed_thread_with_image("u1", image_id="imgA")
    by_id = await find_uploaded_image(thread, "imgA")
    assert by_id is not None
    filename, mime, data = by_id
    assert mime == "image/png"
    assert data == _IMG_B64
    assert filename.endswith(".png")
    # No id -> most recent image.
    recent = await find_uploaded_image(thread, None)
    assert recent is not None and recent[2] == _IMG_B64
    # Unknown id -> None.
    assert await find_uploaded_image(thread, "nope") is None


@pytest.mark.asyncio
async def test_attach_image_bytes_wires_entry_edge(monkeypatch):
    from app.services import attachment_agent

    monkeypatch.setattr(attachment_agent, "policy_evaluate", _async(_Allow()))
    entry = await _seed_entry()

    result = await attach_image_bytes_to_entry(
        user_id="u1",
        entry_id=entry.id,
        filename="pasted-image-imgA.png",
        mime_type="image/png",
        content=_IMG_BYTES,
    )

    assert "error" not in result, result
    att_id = result["attachment"]["id"]
    linked = await entry.nodes(
        edge=[HAS_ATTACHMENT], node=["Attachment"], direction="out"
    )
    assert any(a.id == att_id for a in linked)
    assert att_id in entry.attachment_ids


@pytest.mark.asyncio
async def test_attach_image_bytes_rejects_empty():
    entry = await _seed_entry()
    result = await attach_image_bytes_to_entry(
        user_id="u1",
        entry_id=entry.id,
        filename="x.png",
        mime_type="image/png",
        content=b"",
    )
    assert result.get("error") is True
    assert result["error_code"] == "image_bytes_missing"


@pytest.mark.asyncio
async def test_stager_bakes_image_bytes_from_session():
    """The async stager resolves the thread from the bound session and bakes the
    retained image bytes into the staged payload for the commit-time executor."""
    thread = await _seed_thread_with_image("u1", image_id="imgB")
    token = bindings._propose_session_id.set(thread.provider_session_id)
    try:
        staged = await bindings._stage_attach_uploaded_image(
            {"entry_id": "n.Entry.e1", "image_id": "imgB"}
        )
    finally:
        bindings._propose_session_id.reset(token)

    assert staged["kind"] == "attach_uploaded_image"
    assert staged["payload"]["entry_id"] == "n.Entry.e1"
    assert staged["payload"]["content_b64"] == _IMG_B64
    assert staged["payload"]["mime_type"] == "image/png"
    # base64 must NOT leak into the machine diff (large/noisy).
    assert "content_b64" not in staged["diff_machine"]


@pytest.mark.asyncio
async def test_stager_errors_when_no_image():
    thread = await chat_store.create_thread(
        user_id="u1", provider_id="jvagent", workspace_id="ws1"
    )
    thread.provider_session_id = "sess-noimg"
    await thread.save()
    token = bindings._propose_session_id.set("sess-noimg")
    try:
        with pytest.raises(ValueError):
            await bindings._stage_attach_uploaded_image({"entry_id": "n.Entry.e1"})
    finally:
        bindings._propose_session_id.reset(token)


def test_tool_bound_and_dispatchable():
    binding = bindings.TOOL_BINDINGS.get("integral_attach_uploaded_image_to_entry")
    assert binding is not None
    assert binding.stager is bindings._stage_attach_uploaded_image
