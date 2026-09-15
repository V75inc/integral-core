"""Chat image attachments (Slice A) — schema validation + vision injection.

Images dropped in chat are sent inline as base64 and injected into the turn's
``visitor.data["image_urls"]`` so the jvagent vision reflex can see them. These
tests cover the ``SendMessageRequest`` schema and that ``send_message`` forwards
``image_urls`` to the provider via ``ctx.extra_data``.
"""

from typing import Any, Dict
from unittest.mock import patch

import pytest
from httpx import AsyncClient

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
        json={"provider_id": "jvagent", "agent_id": "aiva"},
    )
    thread_id = create.json()["id"]

    seen: Dict[str, Any] = {}

    async def fake_stream(self, ctx):
        seen["image_urls"] = (ctx.extra_data or {}).get("image_urls")
        if False:
            yield  # async generator, yields nothing

    with patch(
        "app.services.chat_providers.jvagent_provider.JvagentProvider.stream_turn",
        new=fake_stream,
    ):
        resp = await authenticated_client.post(
            f"/api/chat/threads/{thread_id}/messages",
            headers=_scope_headers(workspace_id),
            json={"images": [{"data": PNG_1PX, "content_type": "image/png"}]},
        )
        assert resp.status_code == 200, resp.text

    assert seen.get("image_urls") == [{"base64": PNG_1PX, "mime_type": "image/png"}]
