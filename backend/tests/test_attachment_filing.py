"""Agent-initiated filing: attach a chat-uploaded file (Slice B) to an entry.

`integral_attach_uploaded_file_to_entry` is the deferred filing write-tool
built alongside the composer's general-file upload (Task B4 follow-up). It
wires a second HAS_ATTACHMENT edge from the target Entry onto an
already-uploaded chat attachment (no new bytes) and flips owner_kind so the
entry becomes its canonical home.
"""

from __future__ import annotations

import pytest

from app.agentive.tooling import bindings
from app.api.attachments import _persist_uploaded_chat_file
from app.models.edges import CONTAINS, HAS_ATTACHMENT
from app.models.nodes import Attachment, Entry, Track
from app.services import chat_threads as chat_store
from app.services.attachment_agent import attach_uploaded_file_to_entry


class _Allow:
    allowed = True


class _Deny:
    allowed = False


def _async(value):
    async def _inner(**kwargs):
        return value

    return _inner


def _make_upload(content: bytes, filename: str, content_type: str):
    import io

    from starlette.datastructures import Headers
    from starlette.datastructures import UploadFile as StarletteUploadFile

    return StarletteUploadFile(
        file=io.BytesIO(content),
        filename=filename,
        headers=Headers({"content-type": content_type}),
    )


async def _seed_chat_attachment(user_id: str):
    thread = await chat_store.create_thread(
        user_id=user_id, provider_id="jvagent", workspace_id="ws1"
    )
    upload = _make_upload(b"quarterly numbers", "report.csv", "text/csv")
    result = await _persist_uploaded_chat_file(
        thread=thread, user_id=user_id, file=upload
    )
    return thread, result["attachment"]["id"]


async def _seed_entry():
    track = await Track.create(title="Files", visibility="private")
    entry = await Entry.create(title="Target entry", track_id=track.id)
    await track.connect(entry, edge=CONTAINS)
    return entry


@pytest.mark.asyncio
async def test_attach_uploaded_file_wires_entry_edge(monkeypatch):
    from app.services import attachment_agent

    monkeypatch.setattr(attachment_agent, "policy_evaluate", _async(_Allow()))
    _thread, attachment_id = await _seed_chat_attachment("u1")
    entry = await _seed_entry()

    result = await attach_uploaded_file_to_entry(
        user_id="u1", attachment_id=attachment_id, entry_id=entry.id
    )

    assert "error" not in result
    assert result["attachment"]["id"] == attachment_id
    owners = await Attachment.get(attachment_id)
    assert owners.owner_kind == "entry"

    linked = await entry.nodes(
        edge=[HAS_ATTACHMENT], node=["Attachment"], direction="out"
    )
    assert any(a.id == attachment_id for a in linked)
    assert attachment_id in entry.attachment_ids


@pytest.mark.asyncio
async def test_attach_uploaded_file_stays_reachable_from_thread(monkeypatch):
    """Filing adds a second edge; the original thread ownership isn't severed."""
    from app.services import attachment_agent

    monkeypatch.setattr(attachment_agent, "policy_evaluate", _async(_Allow()))
    thread, attachment_id = await _seed_chat_attachment("u1")
    entry = await _seed_entry()

    await attach_uploaded_file_to_entry(
        user_id="u1", attachment_id=attachment_id, entry_id=entry.id
    )

    still_on_thread = await thread.nodes(
        edge=[HAS_ATTACHMENT], node=["Attachment"], direction="out"
    )
    assert any(a.id == attachment_id for a in still_on_thread)


@pytest.mark.asyncio
async def test_attach_uploaded_file_rejects_non_owner():
    _thread, attachment_id = await _seed_chat_attachment("owner")
    entry = await _seed_entry()

    result = await attach_uploaded_file_to_entry(
        user_id="someone-else", attachment_id=attachment_id, entry_id=entry.id
    )
    assert result.get("error") is True
    assert result["error_code"] == "permission_denied"


@pytest.mark.asyncio
async def test_attach_uploaded_file_rejects_already_filed(monkeypatch):
    from app.services import attachment_agent

    monkeypatch.setattr(attachment_agent, "policy_evaluate", _async(_Allow()))
    _thread, attachment_id = await _seed_chat_attachment("u1")
    entry = await _seed_entry()
    await attach_uploaded_file_to_entry(
        user_id="u1", attachment_id=attachment_id, entry_id=entry.id
    )

    result = await attach_uploaded_file_to_entry(
        user_id="u1", attachment_id=attachment_id, entry_id=entry.id
    )
    assert result.get("error") is True
    assert result["error_code"] == "not_chat_owned"


def test_stager_shapes_staged_change():
    staged = bindings._stage_attach_uploaded_file(
        {"entry_id": "n.Entry.e1", "attachment_id": "n.Attachment.a1"}
    )
    assert staged["kind"] == "attach_uploaded_file"
    assert staged["payload"] == {
        "entry_id": "n.Entry.e1",
        "attachment_id": "n.Attachment.a1",
    }


def test_tool_bound_and_dispatchable():
    binding = bindings.TOOL_BINDINGS.get("integral_attach_uploaded_file_to_entry")
    assert binding is not None
    assert binding.stager is bindings._stage_attach_uploaded_file
