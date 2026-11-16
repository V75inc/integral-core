"""Committed chat delivery obeys current scope, ownership and recovery."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from app.services import chat_threads

pytestmark = pytest.mark.smoke


async def fixture_thread(test_user):
    from app.services.personal_workspace import ensure_personal_workspace

    workspace = await ensure_personal_workspace(test_user)
    # Fixture helper returns a Workspace on this Core candidate.
    thread = await chat_threads.create_thread(
        user_id=test_user.user_id,
        workspace_id=workspace.id,
        provider_id="integral_native",
        title="Disposable delivery fixture",
    )
    return thread


@pytest.mark.asyncio
async def test_append_records_a_committed_chat_invalidation(test_user, monkeypatch):
    thread = await fixture_thread(test_user)
    emit = AsyncMock()
    monkeypatch.setattr("app.services.change_event.emit_change_event", emit)
    message = await chat_threads.append_message(
        thread=thread,
        role="assistant",
        parts=[{"type": "text", "text": "No transcript belongs in notification facts"}],
    )
    assert emit.await_count == 1
    event = emit.await_args.kwargs
    assert event["action"] == "chat.message.create"
    assert event["resource_id"] == thread.id
    assert event["details"] == {"message_id": message.id}
    assert event["before"] is None and event["after"] is None
    assert "No transcript" not in json.dumps(event)


class Socket:
    def __init__(self):
        self.frames = []
        self.closed = False

    async def send_text(self, text):
        self.frames.append(json.loads(text))

    async def close(self, code=1000):
        self.closed = True


async def event_for(thread):
    from app.services.change_event_logger import ChangeEventEnvelope

    message = await chat_threads.append_message(
        thread=thread,
        role="assistant",
        parts=[{"type": "text", "text": "private fixture message"}],
    )
    return ChangeEventEnvelope(
        ts="2026-01-01T00:00:00+00:00",
        actor_kind="agent",
        actor_id=thread.user_id,
        action="chat.message.create",
        resource_type="ChatThread",
        resource_id=thread.id,
        scope=f"user:{thread.user_id}",
        before=None,
        after=None,
        details={"message_id": message.id},
        id="fixture-event",
    )


@pytest.mark.asyncio
async def test_fresh_admission_fans_out_two_owner_sessions_only(test_user):
    from app.agentive.services.agent_events import (
        register_agent_event_connection,
        unregister_agent_event_connection,
    )
    from app.services.chat_thread_event_delivery import queue_committed_chat_event

    thread = await fixture_thread(test_user)
    event = await event_for(thread)
    owner1, owner2, outsider = Socket(), Socket(), Socket()
    for user, socket in [
        (thread.user_id, owner1),
        (thread.user_id, owner2),
        ("o.User.outsider", outsider),
    ]:
        await register_agent_event_connection(user, socket)
    try:
        queue_committed_chat_event(event)
        for _ in range(100):
            if owner1.frames and owner2.frames:
                break
            await asyncio.sleep(0.02)
        assert len(owner1.frames) == len(owner2.frames) == 1
        assert not outsider.frames
        assert "private fixture message" not in json.dumps(owner1.frames)
        assert owner1.frames[0]["payload"]["thread_id"] == thread.id
    finally:
        for user, socket in [
            (thread.user_id, owner1),
            (thread.user_id, owner2),
            ("o.User.outsider", outsider),
        ]:
            await unregister_agent_event_connection(user, socket)


@pytest.mark.asyncio
async def test_deleted_or_wrong_owner_message_and_ambiguous_graph_withhold(test_user):
    from app.models.nodes import ChatMessage
    from app.services.chat_thread_event_delivery import current_chat_payload

    thread = await fixture_thread(test_user)
    event = await event_for(thread)
    assert await current_chat_payload(thread.user_id, event)
    assert await current_chat_payload("o.User.outsider", event) is None
    message = await ChatMessage.get(event.details["message_id"])
    await message.delete()
    assert await current_chat_payload(thread.user_id, event) is None


@pytest.mark.asyncio
async def test_denied_frame_does_not_destroy_socket_for_later_allowed_frame(
    test_user, monkeypatch
):
    from app.agentive.services.agent_events import (
        register_agent_event_connection,
        unregister_agent_event_connection,
    )
    from app.services import chat_thread_event_delivery as delivery

    thread = await fixture_thread(test_user)
    event = await event_for(thread)
    socket = Socket()
    original = delivery.current_chat_payload
    calls = 0

    async def once_denied(user, event):
        nonlocal calls
        calls += 1
        return None if calls == 1 else await original(user, event)

    monkeypatch.setattr(delivery, "current_chat_payload", once_denied)
    await register_agent_event_connection(thread.user_id, socket)
    try:
        delivery.queue_committed_chat_event(event)
        delivery.queue_committed_chat_event(event)
        for _ in range(100):
            if socket.frames:
                break
            await asyncio.sleep(0.02)
        assert calls == 2 and len(socket.frames) == 1 and not socket.closed
    finally:
        await unregister_agent_event_connection(thread.user_id, socket)


@pytest.mark.asyncio
async def test_postgres_rollback_withholds_message_and_event_fact(test_user):
    from jvspatial.core.context import get_default_context

    from app.models.nodes import ChatMessage
    from app.services.app_operations.transaction_scope import (
        graph_transaction_available,
        postgres_graph_transaction,
    )

    if not graph_transaction_available():
        pytest.skip("Requires disposable PostgreSQL transaction fixture")
    thread = await fixture_thread(test_user)
    db = get_default_context().database
    before = {
        r["id"] for r in await db.find("object", {"entity": "OperationEventOutbox"})
    }
    mid = None
    with pytest.raises(RuntimeError, match="rollback fixture"):
        async with postgres_graph_transaction():
            message = await chat_threads.append_message(
                thread=thread,
                role="assistant",
                parts=[{"type": "text", "text": "rolled back"}],
            )
            mid = message.id
            current = get_default_context().database
            assert len(
                await current.find("object", {"entity": "OperationEventOutbox"})
            ) > len(before)
            raise RuntimeError("rollback fixture")
    assert not await ChatMessage.find({"id": mid})
    assert {
        r["id"] for r in await db.find("object", {"entity": "OperationEventOutbox"})
    } == before


@pytest.mark.asyncio
async def test_membership_revocation_withholds_then_restored_membership_admits(
    test_user,
):
    from jvspatial.core.context import get_default_context

    from app.models.edges import IS_MEMBER_OF
    from app.models.nodes import Workspace
    from app.services.app_graph import catalog_workspace
    from app.services.chat_thread_event_delivery import current_chat_payload

    ws = await Workspace.create(
        kind="organization",
        workspace_type="company",
        name="Chat authority fixture",
        name_fold="chat authority fixture",
    )
    await catalog_workspace(ws)
    await test_user.connect(ws, edge=IS_MEMBER_OF, role="owner")
    thread = await chat_threads.create_thread(
        user_id=test_user.user_id, workspace_id=ws.id, provider_id="integral_native"
    )
    event = await event_for(thread)
    assert await current_chat_payload(thread.user_id, event)
    db = get_default_context().database
    edges = await db.find(
        "edge",
        {
            "source": test_user.id,
            "target": ws.id,
            "entity": IS_MEMBER_OF._entity_name(),
        },
    )
    assert len(edges) == 1
    await db.delete("edge", edges[0]["id"])
    assert await current_chat_payload(thread.user_id, event) is None
    await db.save("edge", edges[0])
    assert await current_chat_payload(thread.user_id, event)


@pytest.mark.asyncio
async def test_disabled_identity_withholds_committed_frame(test_user):
    from app.services.authentication import get_authentication_service
    from app.services.chat_thread_event_delivery import current_chat_payload

    thread = await fixture_thread(test_user)
    event = await event_for(thread)
    assert await current_chat_payload(thread.user_id, event)
    service = get_authentication_service()
    user = await service._get_user_by_id(test_user.user_id)
    assert user is not None
    await service.context.database.find_one_and_update(
        "object", {"id": user.id}, {"$set": {"context.is_active": False}}
    )
    assert await current_chat_payload(thread.user_id, event) is None


@pytest.mark.asyncio
async def test_message_from_another_owned_thread_is_not_released(test_user):
    from app.services.chat_thread_event_delivery import current_chat_payload

    thread = await fixture_thread(test_user)
    event = await event_for(thread)
    other = await fixture_thread(test_user)
    other_event = await event_for(other)
    event.details["message_id"] = other_event.details["message_id"]
    assert await current_chat_payload(thread.user_id, event) is None


@pytest.mark.asyncio
async def test_backpressure_disconnects_and_releases_sender_state(test_user):
    from app.agentive.services.agent_events import (
        register_agent_event_connection,
        unregister_agent_event_connection,
    )
    from app.services import chat_thread_event_delivery as delivery

    thread = await fixture_thread(test_user)
    event = await event_for(thread)
    socket = Socket()
    await register_agent_event_connection(thread.user_id, socket)
    try:
        for _ in range(101):
            delivery.queue_committed_chat_event(event)
        for _ in range(100):
            if socket.closed:
                break
            await asyncio.sleep(0.02)
        assert socket.closed
        assert id(socket) not in delivery._queues
        assert id(socket) not in delivery._tasks
    finally:
        await unregister_agent_event_connection(thread.user_id, socket)
