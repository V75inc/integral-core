"""Shared conversation reads and committed wake hints use real graph fixtures."""

import asyncio
from dataclasses import replace

import pytest

from app.agentive.work_models import WorkItem
from app.api.errors import InsufficientPermissionsError, ResourceNotFoundError
from app.services import chat_threads
from app.services.change_event_logger import ChangeEventEnvelope
from app.services.chat_conversation_updates import (
    read_conversation_snapshot,
    subscribe_conversation_updates,
)
from app.services.chat_thread_event_delivery import queue_committed_chat_event
from app.services.chat_turn_transcript import _message_id
from tests.test_access_perimeter_regressions import rooted_graph

pytestmark = pytest.mark.smoke


async def _thread_fixture():
    owner, outsider, workspace, _, _, _ = await rooted_graph()
    thread = await chat_threads.create_thread(
        user_id=owner.id,
        workspace_id=workspace.id,
        provider_id="integral_native",
    )
    return owner, outsider, workspace, thread


async def _work(thread, message, key, *, typed):
    payload = {"accepted_message_id": message.id}
    if typed:
        payload["client_payload_digest"] = "a" * 64
    return await WorkItem.create(
        id=f"o.WorkItem.{key}",
        work_item_id=key,
        kind="chat_turn",
        origin="interactive_chat",
        principal_id=thread.user_id,
        workspace_id=thread.workspace_id,
        thread_id=thread.id,
        input_payload=payload,
    )


def _event(thread, message):
    return ChangeEventEnvelope(
        ts=message.created_at or "",
        actor_kind="human",
        actor_id=thread.user_id,
        action="chat.message.create",
        resource_type="ChatThread",
        resource_id=thread.id,
        scope=f"user:{thread.user_id}",
        workspace_id=thread.workspace_id,
        details={"message_id": message.id},
        id="fixture-committed-chat",
    )


@pytest.mark.asyncio
async def test_snapshot_reconciles_committed_wake_and_fences_typing():
    owner, outsider, workspace, thread = await _thread_fixture()
    scope = (owner.id, workspace.id, thread.id)
    listener = subscribe_conversation_updates(*scope)
    wrong = subscribe_conversation_updates(outsider.id, workspace.id, thread.id)
    try:
        initial = await read_conversation_snapshot(*scope)
        assert initial.updates == ()
        user = await chat_threads.append_message(
            thread=thread,
            role="user",
            parts=[
                {"type": "text", "text": "Please review this document."},
                {
                    "type": "file",
                    "attachment_id": "PRIVATE_FILE_ID",
                    "filename": "PRIVATE_NAME",
                    "mime_type": "application/pdf",
                    "size": 77,
                },
            ],
        )
        await _work(thread, user, "typed-work", typed=True)
        assistant = await chat_threads.append_message(
            thread=thread,
            role="assistant",
            message_id=_message_id("typed-work"),
            parent_id=user.id,
            parts=[
                {"type": "reasoning", "text": "PRIVATE_REASONING"},
                {"type": "tool-call", "args": {"secret": "PRIVATE_TOOL_DATA"}},
                {"type": "text", "text": "Document checked. " + "A" * 500 + "TAIL"},
            ],
        )
        spoken = await chat_threads.append_message(
            thread=thread,
            role="user",
            parts=[{"type": "text", "text": "Spoken request"}],
            provider_metadata={"source": "typed"},
        )
        await _work(thread, spoken, "spoken-work", typed=False)
        spoken_result = await chat_threads.append_message(
            thread=thread,
            role="assistant",
            message_id=_message_id("spoken-work"),
            parent_id=spoken.id,
            parts=[{"type": "text", "text": "Spoken answer"}],
        )
        await chat_threads.append_message(
            thread=thread,
            role="system",
            parts=[{"type": "text", "text": "PRIVATE_CONTROL"}],
        )
        invalidation = _event(thread, assistant)
        queue_committed_chat_event(
            replace(invalidation, workspace_id="unrelated-workspace")
        )
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(listener.wait(), timeout=0.01)
        queue_committed_chat_event(invalidation)
        queue_committed_chat_event(invalidation)
        assert await asyncio.wait_for(listener.wait(), timeout=1) is None
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(wrong.wait(), timeout=0.01)

        snapshot = await read_conversation_snapshot(*scope, cursor=initial.cursor)
        by_id = {item.message_id: item for item in snapshot.updates}
        assert set(by_id) == {user.id, assistant.id, spoken.id, spoken_result.id}
        assert by_id[user.id].source == "typed"
        assert by_id[user.id].kind == "attachments"
        assert by_id[user.id].attachment_count == 1
        assert by_id[assistant.id].source == "typed"
        assert by_id[assistant.id].text.startswith("Document checked.")
        assert len(by_id[assistant.id].text) == 501
        assert by_id[assistant.id].text.endswith("…")
        assert by_id[spoken.id].source == by_id[spoken_result.id].source == "unknown"
        for item in snapshot.updates:
            assert "PRIVATE_" not in item.text
        assert not (
            await read_conversation_snapshot(*scope, cursor=snapshot.cursor)
        ).updates
        assert not snapshot.gap
        with pytest.raises((InsufficientPermissionsError, ResourceNotFoundError)):
            await read_conversation_snapshot(outsider.id, workspace.id, thread.id)
        with pytest.raises(InsufficientPermissionsError):
            await read_conversation_snapshot(*scope, profile_key="another-profile")
    finally:
        listener.close()
        wrong.close()


@pytest.mark.asyncio
async def test_snapshot_reports_gap_without_replaying_and_binds_cursor_to_thread():
    owner, _, workspace, thread = await _thread_fixture()
    scope = (owner.id, workspace.id, thread.id)
    first = await chat_threads.append_message(
        thread=thread, role="user", parts=[{"type": "text", "text": "First"}]
    )
    baseline = await read_conversation_snapshot(*scope)
    assert baseline.updates[0].message_id == first.id
    other = await chat_threads.create_thread(
        user_id=owner.id, workspace_id=workspace.id, provider_id="integral_native"
    )
    with pytest.raises(InsufficientPermissionsError):
        await read_conversation_snapshot(
            owner.id, workspace.id, other.id, cursor=baseline.cursor
        )
    for index in range(14):
        await chat_threads.append_message(
            thread=thread, role="user", parts=[{"type": "text", "text": str(index)}]
        )
    gapped = await read_conversation_snapshot(*scope, cursor=baseline.cursor)
    assert gapped.gap and not gapped.updates
    assert not (await read_conversation_snapshot(*scope, cursor=gapped.cursor)).updates
    # Explicit latest lookups are still available after a missed bounded window.
    assert (await read_conversation_snapshot(*scope)).updates[-1].text == "13"


@pytest.mark.asyncio
async def test_wake_does_not_restore_revoked_workspace_access():
    from jvspatial.core.context import get_default_context

    from app.models.edges import IS_MEMBER_OF

    owner, _, workspace, thread = await _thread_fixture()
    scope = (owner.id, workspace.id, thread.id)
    listener = subscribe_conversation_updates(*scope)
    try:
        baseline = await read_conversation_snapshot(*scope)
        message = await chat_threads.append_message(
            thread=thread, role="user", parts=[{"type": "text", "text": "Now private."}]
        )
        database = get_default_context().database
        edges = await database.find(
            "edge",
            {
                "entity": IS_MEMBER_OF._entity_name(),
                "source": owner.id,
                "target": workspace.id,
            },
        )
        assert len(edges) == 1
        await database.delete("edge", edges[0]["id"])
        queue_committed_chat_event(_event(thread, message))
        await asyncio.wait_for(listener.wait(), timeout=1)
        with pytest.raises(InsufficientPermissionsError):
            await read_conversation_snapshot(*scope, cursor=baseline.cursor)
    finally:
        listener.close()
