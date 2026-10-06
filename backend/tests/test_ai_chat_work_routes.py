"""Authenticated reconnect and cancellation routes for native chat WorkItems."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from app.agentive.work_models import WorkItem
from app.schemas.agentive.chat_events import ChatEventReplayPage


async def _owned_native_thread(
    client: AsyncClient,
) -> tuple[str, str]:
    workspace_response = await client.post(
        "/api/workspaces", json={"name": "Native chat work routes"}
    )
    assert workspace_response.status_code == 200, workspace_response.text
    workspace_id = workspace_response.json()["workspace"]["id"]
    scope = await client.put("/api/users/me/scope", json={"workspace_id": workspace_id})
    assert scope.status_code == 200, scope.text
    thread_response = await client.post(
        "/api/chat/threads",
        headers={"X-Integral-Scope": f"ws:{workspace_id}"},
        json={"provider_id": "jvagent", "agent_id": "aiva"},
    )
    assert thread_response.status_code == 200, thread_response.text
    return workspace_id, thread_response.json()["id"]


def _install_work_item(
    monkeypatch: pytest.MonkeyPatch,
    *,
    work_item_id: str,
    principal_id: str,
    workspace_id: str,
    thread_id: str,
) -> None:
    item = SimpleNamespace(
        work_item_id=work_item_id,
        kind="chat_turn",
        principal_id=principal_id,
        workspace_id=workspace_id,
        thread_id=thread_id,
        status="queued",
        cancel_requested_at="",
    )

    async def get_item(cls, object_id: str) -> Any:
        if object_id == f"o.WorkItem.{work_item_id.removeprefix('o.WorkItem.')}":
            return item
        return None

    monkeypatch.setattr(WorkItem, "get", classmethod(get_item))


@pytest.mark.asyncio
async def test_native_chat_event_replay_is_thread_owned_and_cursor_scoped(
    authenticated_client: AsyncClient, test_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Return only the authorized thread's event page from the requested cursor."""
    workspace_id, thread_id = await _owned_native_thread(authenticated_client)
    work_item_id = "chat-turn:replay-route"
    _install_work_item(
        monkeypatch,
        work_item_id=work_item_id,
        principal_id=test_user.user_id,
        workspace_id=workspace_id,
        thread_id=thread_id,
    )
    page = ChatEventReplayPage(
        events=[{"sequence": 4, "type": "text-delta", "delta": "resumed"}],
        committed_through=4,
        next_after_sequence=4,
        work_status="running",
    )
    replay = AsyncMock(return_value=page)
    monkeypatch.setattr(
        "app.services.chat_turn_events.replay_work_item_chat_events", replay
    )

    response = await authenticated_client.get(
        f"/api/chat/threads/{thread_id}/work-items/{work_item_id}/events",
        params={"after_sequence": 3, "limit": 20},
        headers={"X-Integral-Scope": f"ws:{workspace_id}"},
    )

    assert response.status_code == 200, response.text
    assert response.json() == page.model_dump()
    replay.assert_awaited_once_with(
        principal_id=test_user.user_id,
        workspace_id=workspace_id,
        thread_id=thread_id,
        work_item_id=work_item_id,
        after_sequence=3,
        limit=20,
    )


@pytest.mark.asyncio
async def test_native_chat_event_replay_hides_foreign_work_item(
    authenticated_client: AsyncClient, test_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Conceal a WorkItem owned by a different principal from replay callers."""
    workspace_id, thread_id = await _owned_native_thread(authenticated_client)
    work_item_id = "chat-turn:foreign-route"
    _install_work_item(
        monkeypatch,
        work_item_id=work_item_id,
        principal_id="another-principal",
        workspace_id=workspace_id,
        thread_id=thread_id,
    )
    replay = AsyncMock()
    monkeypatch.setattr(
        "app.services.chat_turn_events.replay_work_item_chat_events", replay
    )

    response = await authenticated_client.get(
        f"/api/chat/threads/{thread_id}/work-items/{work_item_id}/events",
        headers={"X-Integral-Scope": f"ws:{workspace_id}"},
    )

    assert response.status_code == 404
    replay.assert_not_awaited()


@pytest.mark.asyncio
async def test_native_chat_cancel_uses_exact_thread_work_authority(
    authenticated_client: AsyncClient, test_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Request cancellation only after validating the thread's exact WorkItem."""
    workspace_id, thread_id = await _owned_native_thread(authenticated_client)
    work_item_id = "chat-turn:cancel-route"
    _install_work_item(
        monkeypatch,
        work_item_id=work_item_id,
        principal_id=test_user.user_id,
        workspace_id=workspace_id,
        thread_id=thread_id,
    )
    cancelled = SimpleNamespace(
        work_item_id=work_item_id,
        status="running",
        cancel_requested_at="2026-10-04T12:00:00+00:00",
    )
    cancel = AsyncMock(return_value=cancelled)
    monkeypatch.setattr("app.agentive.services.work_items.cancel_work_item", cancel)
    emit = AsyncMock()
    monkeypatch.setattr("app.services.change_event.emit_change_event", emit)

    response = await authenticated_client.post(
        f"/api/chat/threads/{thread_id}/work-items/{work_item_id}/cancel",
        headers={"X-Integral-Scope": f"ws:{workspace_id}"},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "work_item_id": work_item_id,
        "status": "running",
        "cancel_requested": True,
    }
    cancel.assert_awaited_once_with(work_item_id)
    emit.assert_awaited_once_with(
        actor_kind="human",
        actor_id=test_user.user_id,
        action="chat_turn.cancel",
        resource_type="WorkItem",
        resource_id=work_item_id,
        scope=workspace_id,
        before={"status": "queued", "cancel_requested": False},
        after={"status": "running", "cancel_requested": True},
        details={"thread_id": thread_id},
    )
