"""Agent-initiated filing: attach a chat-uploaded file (Slice B) to an entry.

`integral_attach_uploaded_file_to_entry` is the deferred filing write-tool
built alongside the composer's general-file upload (Task B4 follow-up). It
wires a second HAS_ATTACHMENT edge from the target Entry onto an
already-uploaded chat attachment (no new bytes) and flips owner_kind so the
entry becomes its canonical home.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

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
    emit_event = AsyncMock()
    monkeypatch.setattr(attachment_agent, "emit_change_event", emit_event)
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

    emit_event.assert_awaited_once()
    receipt = emit_event.await_args.kwargs
    assert receipt["action"] == "attachment.attach"
    assert receipt["resource_type"] == "Entry"
    assert receipt["resource_id"] == entry.id
    assert receipt["before"] == {"attachment_ids": []}
    assert receipt["after"] == {"attachment_ids": [attachment_id]}
    assert receipt["details"] == {
        "attachment_id": attachment_id,
        "attachment_owner_kind_before": "chat",
    }


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


@pytest.mark.asyncio
async def test_rollback_attachment_filing_preserves_chat_upload(monkeypatch):
    from app.services import attachment_agent, mutation_rollback
    from app.services.change_event_logger import ChangeEventEnvelope

    monkeypatch.setattr(attachment_agent, "policy_evaluate", _async(_Allow()))
    monkeypatch.setattr("app.services.policy_engine.evaluate", _async(_Allow()))
    monkeypatch.setattr(attachment_agent, "emit_change_event", AsyncMock())
    thread, attachment_id = await _seed_chat_attachment("u1")
    entry = await _seed_entry()
    await attach_uploaded_file_to_entry(
        user_id="u1", attachment_id=attachment_id, entry_id=entry.id
    )
    attachment = await Attachment.get(attachment_id)
    envelope = ChangeEventEnvelope(
        id="event-attachment-attach",
        ts="2026-10-05T00:00:00Z",
        actor_kind="human",
        actor_id="u1",
        action="attachment.attach",
        resource_type="Entry",
        resource_id=entry.id,
        scope=f"track:{entry.track_id}",
        before={"attachment_ids": []},
        after={"attachment_ids": [attachment_id]},
        details={
            "attachment_id": attachment_id,
            "attachment_owner_kind_before": "chat",
            "staging_token": "staged-attachment-1",
        },
    )

    result = await mutation_rollback._invert_event(
        envelope=envelope, user_id="u1", force=False
    )

    assert result["method"] == "detach"
    assert result["attachment_id"] == attachment_id
    current_entry = await Entry.get(entry.id)
    assert attachment_id not in current_entry.attachment_ids
    assert not await current_entry.nodes(
        edge=[HAS_ATTACHMENT], node=["Attachment"], direction="out"
    )
    current_attachment = await Attachment.get(attachment_id)
    assert current_attachment.owner_kind == "chat"
    thread_attachments = await thread.nodes(
        edge=[HAS_ATTACHMENT], node=["Attachment"], direction="out"
    )
    assert any(item.id == attachment_id for item in thread_attachments)


@pytest.mark.asyncio
async def test_rollback_attachment_filing_refuses_to_orphan_attachment(monkeypatch):
    from app.services import attachment_agent, mutation_rollback
    from app.services.change_event_logger import ChangeEventEnvelope

    monkeypatch.setattr(attachment_agent, "policy_evaluate", _async(_Allow()))
    monkeypatch.setattr("app.services.policy_engine.evaluate", _async(_Allow()))
    monkeypatch.setattr(attachment_agent, "emit_change_event", AsyncMock())
    thread, attachment_id = await _seed_chat_attachment("u1")
    entry = await _seed_entry()
    await attach_uploaded_file_to_entry(
        user_id="u1", attachment_id=attachment_id, entry_id=entry.id
    )
    context = await thread.get_context()
    thread_edges = await context.find_edges_between(
        thread.id, attachment_id, edge_class=HAS_ATTACHMENT
    )
    for edge in thread_edges:
        await edge.delete()
    envelope = ChangeEventEnvelope(
        id="event-attachment-attach",
        ts="2026-10-05T00:00:00Z",
        actor_kind="human",
        actor_id="u1",
        action="attachment.attach",
        resource_type="Entry",
        resource_id=entry.id,
        scope=f"track:{entry.track_id}",
        before={"attachment_ids": []},
        after={"attachment_ids": [attachment_id]},
        details={
            "attachment_id": attachment_id,
            "attachment_owner_kind_before": "chat",
        },
    )

    with pytest.raises(mutation_rollback.RollbackError) as exc:
        await mutation_rollback._invert_event(
            envelope=envelope, user_id="u1", force=False
        )

    assert exc.value.code == "conflict"
    current_entry = await Entry.get(entry.id)
    assert attachment_id in current_entry.attachment_ids
    linked = await current_entry.nodes(
        edge=[HAS_ATTACHMENT], node=["Attachment"], direction="out"
    )
    assert any(item.id == attachment_id for item in linked)


@pytest.mark.asyncio
async def test_stager_shapes_staged_change():
    staged = await bindings._stage_attach_uploaded_file(
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


@pytest.mark.asyncio
async def test_attachment_stage_accepts_valid_backward_target_without_writes(
    monkeypatch,
):
    from app.agentive import staging
    from app.services.agent_scope import current_scope_workspace_id

    staging._reset_for_tests()
    thread, attachment_id = await _seed_chat_attachment("owner")
    await staging.open_batch(
        user_id="owner", session_id=thread.id, label="File receipt"
    )
    await staging.append_to_batch(
        user_id="owner",
        session_id=thread.id,
        op={
            "kind": "create_entry",
            "payload": {"track_id": "track", "title": "Receipt"},
        },
    )
    monkeypatch.setattr(
        chat_store, "get_thread_by_session", AsyncMock(return_value=thread)
    )
    principal = bindings._propose_principal.set("owner")
    session = bindings._propose_session_id.set(thread.id)
    workspace = current_scope_workspace_id.set("ws1")
    try:
        staged = await bindings._stage_attach_uploaded_file(
            {
                "entry_id": "{{step_1.id}}",
                "attachment_id": attachment_id,
            }
        )
        assert staged["payload"]["entry_id"] == "{{step_1.id}}"
        attachment = await Attachment.get(attachment_id)
        assert attachment.owner_kind == "chat"
        assert not await attachment.nodes(
            edge=[HAS_ATTACHMENT], node=["Entry"], direction="in", limit=1
        )
        workspace_wrong = current_scope_workspace_id.set("other-workspace")
        try:
            with pytest.raises(Exception, match="own this file"):
                await bindings._stage_attach_uploaded_file(
                    {
                        "entry_id": "{{step_1.id}}",
                        "attachment_id": attachment_id,
                    }
                )
        finally:
            current_scope_workspace_id.reset(workspace_wrong)
        other, _ = await _seed_chat_attachment("owner")
        monkeypatch.setattr(
            chat_store, "get_thread_by_session", AsyncMock(return_value=other)
        )
        with pytest.raises(Exception, match="own this file"):
            await bindings._stage_attach_uploaded_file(
                {
                    "entry_id": "{{step_1.id}}",
                    "attachment_id": attachment_id,
                }
            )
    finally:
        bindings._propose_principal.reset(principal)
        bindings._propose_session_id.reset(session)
        current_scope_workspace_id.reset(workspace)
        staging._reset_for_tests()
