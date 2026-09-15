"""Chat-owned Attachment must be graph-reachable via its ChatThread (I-GRAPH-01).

Slice B (chat file upload) reuses the Attachment node + HAS_ATTACHMENT edge
for chat-thread-scoped files instead of introducing a parallel node/edge
pair. ``owner_kind`` distinguishes chat-owned rows from entry-owned rows;
the edge source is a ``ChatThread`` instead of an ``Entry``.
"""

import pytest

from app.models.edges import HAS_ATTACHMENT
from app.models.nodes import Attachment, ChatThread
from app.services import chat_threads as chat_store


@pytest.mark.asyncio
async def test_attachment_owner_kind_defaults_to_entry():
    """Back-compat: existing entry-attachment call sites don't set owner_kind."""
    attachment = await Attachment.create(
        filename="doc.pdf", mime_type="application/pdf"
    )
    assert attachment.owner_kind == "entry"


@pytest.mark.asyncio
async def test_chat_attachment_wired_from_thread():
    """A chat-owned Attachment attaches to its ChatThread via HAS_ATTACHMENT."""
    thread = await chat_store.create_thread(
        user_id="u1",
        provider_id="jvagent",
        workspace_id="ws1",
    )
    attachment = await Attachment.create(
        filename="report.csv",
        mime_type="text/csv",
        owner_kind="chat",
    )
    await thread.connect(attachment, edge=HAS_ATTACHMENT)

    children = await thread.nodes(
        edge=[HAS_ATTACHMENT],
        node=["Attachment"],
        direction="out",
    )
    assert any(a.id == attachment.id for a in children)


@pytest.mark.asyncio
async def test_chat_attachment_reachable_from_thread_reverse_edge():
    """The attachment can find its owning thread by walking the edge in."""
    thread = await chat_store.create_thread(
        user_id="u1",
        provider_id="jvagent",
        workspace_id="ws1",
    )
    attachment = await Attachment.create(
        filename="image.png",
        mime_type="image/png",
        owner_kind="chat",
    )
    await thread.connect(attachment, edge=HAS_ATTACHMENT)

    owners = await attachment.nodes(
        edge=[HAS_ATTACHMENT],
        node=["ChatThread"],
        direction="in",
    )
    assert len(owners) == 1
    assert isinstance(owners[0], ChatThread)
    assert owners[0].id == thread.id
