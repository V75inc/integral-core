"""Public durable-work observation contract."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.agentive.services import work_items


async def _workspace_id(client: AsyncClient, name: str = "Work API") -> str:
    response = await client.post("/api/workspaces", json={"name": name})
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


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_work_list_is_scoped_safe_and_paginated(authenticated_client, test_user):
    workspace_id = await _workspace_id(authenticated_client)
    other_workspace = await _workspace_id(authenticated_client, "Other Work API")
    for index in range(3):
        await work_items.enqueue_work_item(
            kind="app_lifecycle",
            origin="test",
            principal_id=test_user.id,
            workspace_id=workspace_id,
            idempotency_key=f"page-{index}",
            input_payload={"install_token": "must-not-leak"},
        )
    hidden = await work_items.enqueue_work_item(
        kind="app_lifecycle",
        origin="test",
        principal_id=test_user.id,
        workspace_id=other_workspace,
        idempotency_key="other-workspace",
        input_payload={},
    )
    response = await authenticated_client.get(
        "/api/work-items", params={"workspace_id": workspace_id, "limit": 2}
    )
    assert response.status_code == 200, response.text
    page = response.json()
    assert len(page["items"]) == 2 and page["next_cursor"]
    assert (
        "must-not-leak" not in response.text
        and hidden.work_item_id not in response.text
    )
    second = await authenticated_client.get(
        "/api/work-items",
        params={
            "workspace_id": workspace_id,
            "limit": 2,
            "cursor": page["next_cursor"],
        },
    )
    assert second.status_code == 200, second.text
    assert len(second.json()["items"]) == 1
    assert not set(x["work_item_id"] for x in page["items"]) & set(
        x["work_item_id"] for x in second.json()["items"]
    )


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_work_list_denies_outside_workspace(
    authenticated_client, second_user_client
):
    workspace_id = await _workspace_id(authenticated_client)
    response = await second_user_client.get(
        "/api/work-items", params={"workspace_id": workspace_id}
    )
    assert response.status_code == 404, response.text


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_regular_member_lists_only_own_work(
    authenticated_client, test_user, monkeypatch
):
    from app.agentive.services import work_visibility

    workspace_id = await _workspace_id(authenticated_client)
    own = await work_items.enqueue_work_item(
        kind="app_lifecycle",
        origin="test",
        principal_id=test_user.user_id,
        workspace_id=workspace_id,
        idempotency_key="own",
        input_payload={},
    )
    await work_items.enqueue_work_item(
        kind="app_lifecycle",
        origin="test",
        principal_id="other-principal",
        workspace_id=workspace_id,
        idempotency_key="other",
        input_payload={},
    )

    async def member(*args):
        return "member"

    monkeypatch.setattr(work_visibility, "can_access_workspace", member)
    response = await authenticated_client.get(
        "/api/work-items", params={"workspace_id": workspace_id}
    )
    assert response.status_code == 200, response.text
    assert [x["work_item_id"] for x in response.json()["items"]] == [own.work_item_id]


@pytest.mark.smoke
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params",
    [
        {},
        {"workspace_id": "workspace", "limit": 0},
        {"workspace_id": "workspace", "limit": 101},
    ],
)
async def test_work_list_requires_bounded_workspace(authenticated_client, params):
    response = await authenticated_client.get("/api/work-items", params=params)
    assert response.status_code == 400, response.text
