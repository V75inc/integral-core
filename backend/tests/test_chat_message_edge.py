"""ChatMessage MUST be reachable from its ChatThread via a CONTAINS edge."""

import pytest

from app.models.edges import CONTAINS
from app.models.nodes import ChatMessage, ChatThread
from app.services import chat_threads as chat_store


@pytest.mark.asyncio
async def test_append_message_creates_contains_edge():
    """append_message wires CONTAINS from thread to message."""
    thread = await chat_store.create_thread(
        user_id="u1",
        provider_id="jvagent",
        workspace_id="ws1",
    )
    message = await chat_store.append_message(
        thread=thread,
        role="user",
        parts=[{"type": "text", "text": "hi"}],
    )
    children = await thread.nodes(
        edge=[CONTAINS],
        node=["ChatMessage"],
        direction="out",
    )
    assert any(m.id == message.id for m in children)


@pytest.mark.asyncio
async def test_list_messages_via_edge_traversal():
    """list_messages reads from the CONTAINS edge, not the scalar."""
    thread = await chat_store.create_thread(
        user_id="u1",
        provider_id="jvagent",
        workspace_id="ws1",
    )
    for i in range(3):
        await chat_store.append_message(
            thread=thread,
            role="user",
            parts=[{"type": "text", "text": str(i)}],
        )
    messages = await chat_store.list_messages(thread)
    assert len(messages) == 3
    assert [m.parts[0]["text"] for m in messages] == ["0", "1", "2"]


@pytest.mark.asyncio
async def test_delete_thread_messages_via_edge_traversal():
    thread = await chat_store.create_thread(
        user_id="u1",
        provider_id="jvagent",
        workspace_id="ws1",
    )
    for _ in range(2):
        await chat_store.append_message(
            thread=thread,
            role="user",
            parts=[],
        )
    deleted = await chat_store.delete_thread_messages(thread)
    assert deleted == 2
    remaining = await chat_store.list_messages(thread)
    assert remaining == []


@pytest.mark.asyncio
async def test_message_thread_id_scalar_still_set_as_cache():
    """thread_id scalar stays as denormalized cache for serialization."""
    thread = await chat_store.create_thread(
        user_id="u1",
        provider_id="jvagent",
        workspace_id="ws1",
    )
    message = await chat_store.append_message(
        thread=thread,
        role="user",
        parts=[],
    )
    assert message.thread_id == thread.id
