"""Public durable-work observation contract."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.agentive.services import work_items


async def _workspace_id(client: AsyncClient) -> str:
    response = await client.post("/api/workspaces", json={"name": "Work API"})
    assert response.status_code in (200, 201), response.text
    return response.json()["workspace"]["id"]


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_initiator_can_read_safe_work_status(
    authenticated_client: AsyncClient, test_user
) -> None:
    workspace_id = await _workspace_id(authenticated_client)
    item = await work_items.enqueue_work_item(
        kind="app_lifecycle",
        origin="test",
        principal_id=test_user.id,
        workspace_id=workspace_id,
        idempotency_key="safe-observation",
        input_payload={"install_token": "must-not-leak"},
    )

    response = await authenticated_client.get(f"/api/work-items/{item.work_item_id}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["work_item_id"] == item.work_item_id
    assert body["status"] == "queued"
    assert body["workspace_id"] == workspace_id
    assert "input_payload" not in body
    assert "install_token" not in response.text


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_stranger_cannot_observe_work_item(
    authenticated_client: AsyncClient, second_user_client: AsyncClient, test_user
) -> None:
    workspace_id = await _workspace_id(authenticated_client)
    item = await work_items.enqueue_work_item(
        kind="app_lifecycle",
        origin="test",
        principal_id=test_user.id,
        workspace_id=workspace_id,
        idempotency_key="hidden-observation",
        input_payload={},
    )

    response = await second_user_client.get(f"/api/work-items/{item.work_item_id}")

    assert response.status_code == 404, response.text
