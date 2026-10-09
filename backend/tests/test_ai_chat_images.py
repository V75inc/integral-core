"""Chat image attachments (Slice A) — schema validation + vision injection.

Images dropped in chat are sent inline as base64 and injected into the turn's
``visitor.data["image_urls"]`` so the native provider vision reflex can see them. These
tests cover the ``SendMessageRequest`` schema and that ``send_message`` forwards
``image_urls`` to the provider via ``ctx.extra_data``.
"""

from typing import Any, Dict
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from app.models.nodes import ChatThread
from app.schemas.api.ai_chat import SendMessageRequest

PNG_1PX = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42m\n"
    "NkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
).replace("\n", "")


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_accepts_image_only_message():
    req = SendMessageRequest.model_validate(
        {"images": [{"data": PNG_1PX, "content_type": "image/png"}]}
    )
    assert req.text == ""
    assert req.images and req.images[0].content_type == "image/png"


def test_rejects_empty_text_and_no_images():
    with pytest.raises(ValueError):
        SendMessageRequest.model_validate({"text": "", "images": None})


pytestmark = pytest.mark.usefixtures("standalone_chat_provider")


@pytest.mark.parametrize(
    "host_action", ["prompt_sheet_resume", "staging_follow_through"]
)
def test_host_continuation_can_start_without_user_text(host_action: str):
    req = SendMessageRequest.model_validate({"text": "", "host_action": host_action})
    assert req.text == ""
    assert req.host_action == host_action


def test_unknown_host_action_is_rejected():
    with pytest.raises(ValueError):
        SendMessageRequest.model_validate(
            {"text": "", "host_action": "pretend_user_said_go_ahead"}
        )


def test_rejects_unsupported_image_type():
    with pytest.raises(ValueError):
        SendMessageRequest.model_validate(
            {"images": [{"data": PNG_1PX, "content_type": "image/tiff"}]}
        )


def test_rejects_too_many_images():
    imgs = [{"data": PNG_1PX, "content_type": "image/png"} for _ in range(5)]
    with pytest.raises(ValueError):
        SendMessageRequest.model_validate({"images": imgs})


def test_strips_data_url_prefix():
    req = SendMessageRequest.model_validate(
        {
            "images": [
                {
                    "data": f"data:image/png;base64,{PNG_1PX}",
                    "content_type": "image/png",
                }
            ]
        }
    )
    assert req.images and req.images[0].data == PNG_1PX


# ---------------------------------------------------------------------------
# Endpoint — image_urls reaches the provider via extra_data
# ---------------------------------------------------------------------------


async def _create_workspace(client: AsyncClient) -> str:
    resp = await client.post("/api/workspaces", json={"name": "Images WS"})
    assert resp.status_code == 200, resp.text
    workspace_id = resp.json()["workspace"]["id"]
    scope = await client.put("/api/users/me/scope", json={"workspace_id": workspace_id})
    assert scope.status_code == 200, scope.text
    return workspace_id


def _scope_headers(workspace_id: str) -> Dict[str, str]:
    return {"X-Integral-Scope": f"ws:{workspace_id}"}


@pytest.mark.asyncio
async def test_send_message_injects_image_urls(
    authenticated_client: AsyncClient, test_user
):
    """An image-only turn forwards visitor image_urls to the provider."""
    workspace_id = await _create_workspace(authenticated_client)
    create = await authenticated_client.post(
        "/api/chat/threads",
        headers=_scope_headers(workspace_id),
        json={"provider_id": "test-provider", "agent_id": "aiva"},
    )
    thread_id = create.json()["id"]

    seen: Dict[str, Any] = {}

    async def fake_stream(self, ctx):
        seen["image_urls"] = (ctx.extra_data or {}).get("image_urls")
        if False:
            yield  # async generator, yields nothing

    with patch(
        "tests.chat_provider_double.StandaloneTestProvider.stream_turn",
        new=fake_stream,
    ):
        resp = await authenticated_client.post(
            f"/api/chat/threads/{thread_id}/messages",
            headers=_scope_headers(workspace_id),
            json={"images": [{"data": PNG_1PX, "content_type": "image/png"}]},
        )
        assert resp.status_code == 200, resp.text

    assert seen.get("image_urls") == [{"base64": PNG_1PX, "mime_type": "image/png"}]


@pytest.mark.asyncio
async def test_host_policy_stays_out_of_user_utterance(
    authenticated_client: AsyncClient,
):
    workspace_id = await _create_workspace(authenticated_client)
    create = await authenticated_client.post(
        "/api/chat/threads",
        headers=_scope_headers(workspace_id),
        json={"provider_id": "test-provider", "agent_id": "aiva"},
    )
    thread_id = create.json()["id"]
    user_text = (
        "Please do not save anything; keep it in chat only. Explain the next step."
    )
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
            json={"text": user_text},
        )
        assert resp.status_code == 200, resp.text

    assert seen["text"] == user_text
    assert "SYSTEM:USER_FORBIDS_SAVING" in seen["system_context"]
    assert "Do not call a write, batch, proposal" in seen["system_context"]
    assert "SYSTEM:USER_FORBIDS_SAVING" not in seen["text"]

    transcript = await authenticated_client.get(
        f"/api/chat/threads/{thread_id}", headers=_scope_headers(workspace_id)
    )
    assert transcript.status_code == 200, transcript.text
    user_messages = [
        message
        for message in transcript.json()["messages"]
        if message["role"] == "user"
    ]
    assert len(user_messages) == 1
    assert user_messages[0]["parts"] == [{"type": "text", "text": user_text}]


@pytest.mark.asyncio
async def test_prompt_sheet_resume_uses_host_context_without_user_utterance(
    authenticated_client: AsyncClient,
):
    workspace_id = await _create_workspace(authenticated_client)
    create = await authenticated_client.post(
        "/api/chat/threads",
        headers=_scope_headers(workspace_id),
        json={"provider_id": "test-provider", "agent_id": "aiva"},
    )
    thread_id = create.json()["id"]
    thread = await ChatThread.get(thread_id)
    thread.prompt_queue = {
        "status": "closed",
        "close_reason": "drained",
        "items": [
            {
                "kind": "staged_write",
                "status": "approved",
                "write_kind": "create_entry",
                "summary": "Create the synthetic Venture record",
            }
        ],
    }
    await thread.save()

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
            json={"text": "", "host_action": "prompt_sheet_resume"},
        )
        assert resp.status_code == 200, resp.text

    assert seen["text"] == ""
    assert "prompt_sheet_result" in seen["system_context"]
    assert "prompt_sheet_continuation" in seen["system_context"]
    transcript = await authenticated_client.get(
        f"/api/chat/threads/{thread_id}", headers=_scope_headers(workspace_id)
    )
    assert transcript.status_code == 200, transcript.text
    user_messages = [
        message
        for message in transcript.json()["messages"]
        if message["role"] == "user"
    ]
    assert user_messages == []


@pytest.mark.asyncio
async def test_native_pending_authority_uses_conversation_not_checkpoint(monkeypatch):
    from types import SimpleNamespace

    from app.api.ai_chat import _pending_staged_for_turn

    thread = SimpleNamespace(
        id="n.ChatThread.a",
        provider_id="integral_native",
        provider_session_id="rotated-checkpoint",
        workspace_id="workspace-a",
    )
    own = SimpleNamespace(session_id=thread.id, workspace_id=thread.workspace_id)
    foreign = SimpleNamespace(session_id=thread.id, workspace_id="workspace-b")
    other_conversation = SimpleNamespace(
        session_id="n.ChatThread.b", workspace_id=thread.workspace_id
    )

    async def pending(user_id):
        assert user_id == "user-a"
        return [own, foreign, other_conversation]

    monkeypatch.setattr("app.agentive.staging.get_pending_for_user", pending)
    assert await _pending_staged_for_turn("user-a", thread) == [own]
