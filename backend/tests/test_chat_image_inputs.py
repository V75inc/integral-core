"""Pixels must originate in a currently readable canonical chat upload."""

import base64
import hashlib
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image
from pydantic_ai import BinaryContent

from app.agentive.harness.contracts import HarnessExecutionScope
from app.agentive.harness.model_errors import NativeModelRequestError
from app.models.edges import CONTAINS, HAS_ATTACHMENT
from app.schemas.agentive.work import WorkError
from app.services import chat_image_inputs as images

pytestmark = pytest.mark.asyncio


@pytest.fixture
def visual(monkeypatch):
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), "orange").save(buffer, "PNG")
    data = buffer.getvalue()
    scope = HarnessExecutionScope(
        tenant_id="w",
        workspace_id="w",
        principal_id="u",
        thread_id="t",
        session_id="s",
        run_id="r",
        permission_revision="p",
        capability_version="c",
    )
    graph = SimpleNamespace(existing=True)
    thread = SimpleNamespace(
        id="t", user_id="u", workspace_id="w", get_context=AsyncMock(return_value=graph)
    )
    attachment = SimpleNamespace(
        id="a",
        filename="neutral.png",
        mime_type="image/png",
        size=len(data),
        source_type="file",
        uploaded_by="u",
        scan_status="clean",
        storage_key="key",
        content_hash=hashlib.sha256(data).hexdigest(),
    )
    message = SimpleNamespace(
        id="m",
        thread_id="t",
        role="user",
        parts=[{"type": "file", "attachment_id": "a", "mime_type": "image/png"}],
    )
    storage = SimpleNamespace(read_attachment=AsyncMock(return_value=data))
    owners = [{"source": "t", "target": "a", "entity": HAS_ATTACHMENT._entity_name()}]

    async def find_edges(_collection, query, **_kwargs):
        if not graph.existing:
            return []
        rows = (
            owners
            if query.get("target") == "a"
            else [{"source": "t", "target": "m", "entity": CONTAINS._entity_name()}]
        )
        return [row for row in rows if all(row.get(k) == v for k, v in query.items())]

    graph.database = SimpleNamespace(find=AsyncMock(side_effect=find_edges))

    async def fresh_node(model, node_id):
        return {
            images.ChatThread: thread,
            images.ChatMessage: message,
            images.Attachment: attachment,
            images.Entry: SimpleNamespace(id="e"),
        }.get(model)

    monkeypatch.setattr(images, "_fresh_node", AsyncMock(side_effect=fresh_node))
    monkeypatch.setattr(images.Attachment, "find", AsyncMock(return_value=[attachment]))
    monkeypatch.setattr(
        "app.services.workspace_permissions.can_access_workspace",
        AsyncMock(return_value="owner"),
    )
    monkeypatch.setattr(
        "app.services.attachment_agent._require_entry_read", AsyncMock()
    )
    monkeypatch.setattr(
        "app.services.resource_location.locate_resource",
        AsyncMock(return_value=SimpleNamespace(workspace_id="w")),
    )
    monkeypatch.setattr(
        "app.services.attachment_storage.get_attachment_storage_service",
        lambda: storage,
    )
    uri = "data:image/png;base64," + base64.b64encode(data).decode()
    payload = {
        "messages": [
            {
                "role": "user",
                "content": [{"type": "image_url", "image_url": {"url": uri}}],
            }
        ]
    }
    return SimpleNamespace(
        scope=scope,
        thread=thread,
        attachment=attachment,
        message=message,
        storage=storage,
        graph=graph,
        owners=owners,
        data=data,
        payload=payload,
    )


async def test_pixels_and_authored_text_reach_typed_prompt(visual):
    prompt = await images.chat_image_prompt(visual.scope, "m", "Compare these shapes")
    assert prompt[0] == "Compare these shapes"
    assert isinstance(prompt[-1], BinaryContent)
    assert prompt[-1].data == visual.data
    await images.assert_model_image_access(visual.scope, visual.payload)


async def test_image_only_prompt_is_not_an_empty_continuation(visual):
    prompt = await images.chat_image_prompt(visual.scope, "m", "")
    assert any(isinstance(part, BinaryContent) for part in prompt)


@pytest.mark.parametrize(
    "field,value", [("user_id", "other"), ("workspace_id", "other")]
)
async def test_rejects_foreign_chat_scope(visual, field, value):
    setattr(visual.thread, field, value)
    with pytest.raises(WorkError):
        await images.chat_image_prompt(visual.scope, "m", "Describe")
    visual.storage.read_attachment.assert_not_awaited()


async def test_rejects_revoked_membership(visual, monkeypatch):
    monkeypatch.setattr(
        "app.services.workspace_permissions.can_access_workspace",
        AsyncMock(return_value="none"),
    )
    with pytest.raises(WorkError):
        await images.assert_model_image_access(visual.scope, visual.payload)


async def test_rejects_detached_message_or_upload(visual):
    visual.graph.existing = False
    with pytest.raises(WorkError):
        await images.chat_image_prompt(visual.scope, "m", "Describe")
    with pytest.raises(WorkError):
        await images.assert_model_image_access(visual.scope, visual.payload)


@pytest.mark.parametrize(
    "field,value",
    [("uploaded_by", "other"), ("scan_status", "blocked"), ("content_hash", "0" * 64)],
)
async def test_rejects_changed_or_unsafe_upload(visual, field, value):
    setattr(visual.attachment, field, value)
    with pytest.raises(WorkError):
        await images.chat_image_prompt(visual.scope, "m", "Describe")


async def test_rechecks_old_image_and_storage_on_every_dispatch(visual):
    await images.assert_model_image_access(visual.scope, visual.payload)
    visual.storage.read_attachment.return_value = b"changed"
    with pytest.raises(WorkError):
        await images.assert_model_image_access(visual.scope, visual.payload)
    assert visual.storage.read_attachment.await_count == 2


async def test_entry_read_denial_prevents_image_dispatch(visual, monkeypatch):
    visual.owners.append(
        {"source": "e", "target": "a", "entity": HAS_ATTACHMENT._entity_name()}
    )
    monkeypatch.setattr(
        "app.services.attachment_agent._require_entry_read",
        AsyncMock(side_effect=PermissionError),
    )
    with pytest.raises(WorkError):
        await images.assert_model_image_access(visual.scope, visual.payload)


async def test_foreign_entry_parent_is_rejected(visual, monkeypatch):
    visual.owners.append(
        {"source": "e", "target": "a", "entity": HAS_ATTACHMENT._entity_name()}
    )
    monkeypatch.setattr(
        "app.services.resource_location.locate_resource",
        AsyncMock(return_value=SimpleNamespace(workspace_id="foreign")),
    )
    with pytest.raises(WorkError):
        await images.chat_image_prompt(visual.scope, "m", "Describe")


@pytest.mark.parametrize(
    "uri",
    [
        "https://example.com/private.png",
        "data:image/svg+xml;base64,AAAA",
        "data:image/png;base64,%%%",
    ],
)
async def test_no_arbitrary_urls_or_invalid_inline_payload(visual, uri):
    visual.payload["messages"][0]["content"][0]["image_url"]["url"] = uri
    with pytest.raises(NativeModelRequestError):
        await images.assert_model_image_access(visual.scope, visual.payload)
    visual.storage.read_attachment.assert_not_awaited()


async def test_forged_image_not_present_on_thread_is_rejected(visual):
    images.Attachment.find.return_value = []
    with pytest.raises(WorkError):
        await images.assert_model_image_access(visual.scope, visual.payload)


async def test_corrupt_file_is_not_forwarded(visual):
    visual.storage.read_attachment.return_value = b"not a png"
    visual.attachment.size = 9
    visual.attachment.content_hash = hashlib.sha256(b"not a png").hexdigest()
    with pytest.raises(NativeModelRequestError, match="image_input_invalid"):
        await images.chat_image_prompt(visual.scope, "m", "Describe")


async def test_image_count_bound(visual):
    visual.message.parts *= 5
    with pytest.raises(NativeModelRequestError, match="image_input_limit"):
        await images.chat_image_prompt(visual.scope, "m", "Describe")


async def test_nonvisual_turn_needs_no_image_reads(visual):
    assert await images.chat_image_prompt(visual.scope, None, "Hello") == "Hello"
    await images.assert_model_image_access(
        visual.scope, {"messages": [{"role": "user", "content": "Hello"}]}
    )
    visual.storage.read_attachment.assert_not_awaited()


@pytest.mark.parametrize("text", ["", "Approval outcome received."])
async def test_system_continuation_accepts_text_without_new_images(visual, text):
    visual.message.role = "system"
    visual.message.parts = [{"type": "text", "text": "Approval outcome received."}]
    assert await images.chat_image_prompt(visual.scope, "m", text) == (text or None)
    visual.storage.read_attachment.assert_not_awaited()


@pytest.mark.parametrize("role", ["system", "assistant", "tool"])
async def test_non_user_messages_cannot_introduce_image_pixels(visual, role):
    visual.message.role = role
    with pytest.raises(WorkError):
        await images.chat_image_prompt(visual.scope, "m", "Describe")
    visual.storage.read_attachment.assert_not_awaited()


async def test_system_continuation_cannot_reference_another_thread(visual):
    visual.message.role = "system"
    visual.message.thread_id = "other"
    visual.message.parts = []
    with pytest.raises(WorkError):
        await images.chat_image_prompt(visual.scope, "m", "")


async def test_fresh_image_state_bypasses_hydrated_node_cache(monkeypatch):
    stale = SimpleNamespace(scan_status="clean")
    database = SimpleNamespace(
        find=AsyncMock(
            return_value=[
                {
                    "id": "a",
                    "entity": "Attachment",
                    "context": {"scan_status": "blocked"},
                }
            ]
        )
    )

    async def deserialize(_model, row):
        return SimpleNamespace(**row["context"])

    graph = SimpleNamespace(database=database, _deserialize_entity=deserialize)
    monkeypatch.setattr("jvspatial.core.context.get_default_context", lambda: graph)
    monkeypatch.setattr(images.Attachment, "get", AsyncMock(return_value=stale))
    current = await images._fresh_node(images.Attachment, "a")
    assert current.scan_status == "blocked"
    images.Attachment.get.assert_not_awaited()
    database.find.return_value = [
        {"id": "a", "entity": "Attachment", "context": {"scan_status": "clean"}}
    ]
    assert (await images._fresh_node(images.Attachment, "a")).scan_status == "clean"
