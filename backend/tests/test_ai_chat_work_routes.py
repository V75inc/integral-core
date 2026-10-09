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
        json={"provider_id": "test-provider", "agent_id": "aiva"},
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


pytestmark = pytest.mark.usefixtures("standalone_chat_provider")


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


@pytest.mark.asyncio
async def test_native_chat_stream_replays_committed_cursor(
    authenticated_client: AsyncClient, test_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id, thread_id = await _owned_native_thread(authenticated_client)
    work_item_id = "chat-turn:stream-route"
    _install_work_item(
        monkeypatch,
        work_item_id=work_item_id,
        principal_id=test_user.user_id,
        workspace_id=workspace_id,
        thread_id=thread_id,
    )
    replay = AsyncMock(
        return_value=ChatEventReplayPage(
            events=[{"sequence": 4, "type": "text-delta", "delta": "Saved response"}],
            committed_through=4,
            next_after_sequence=4,
            work_status="succeeded",
        )
    )
    monkeypatch.setattr(
        "app.services.chat_turn_events.replay_work_item_chat_events", replay
    )
    monkeypatch.setattr(
        "app.services.chat_turn_streaming.replay_work_item_chat_events", replay
    )
    response = await authenticated_client.get(
        f"/api/chat/threads/{thread_id}/work-items/{work_item_id}/stream",
        params={"after_sequence": 3},
        headers={"X-Integral-Scope": f"ws:{workspace_id}"},
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "id: 4\n" in response.text
    assert "Saved response" in response.text
    assert '"committed_through": 4' in response.text
    assert replay.await_count == 2
    assert all(call.kwargs["after_sequence"] == 3 for call in replay.await_args_list)


@pytest.mark.asyncio
async def test_native_chat_stream_denies_foreign_item_before_headers(
    authenticated_client: AsyncClient, test_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id, thread_id = await _owned_native_thread(authenticated_client)
    work_item_id = "chat-turn:foreign-stream"
    _install_work_item(
        monkeypatch,
        work_item_id=work_item_id,
        principal_id="another-user",
        workspace_id=workspace_id,
        thread_id=thread_id,
    )
    replay = AsyncMock()
    monkeypatch.setattr(
        "app.services.chat_turn_events.replay_work_item_chat_events", replay
    )
    response = await authenticated_client.get(
        f"/api/chat/threads/{thread_id}/work-items/{work_item_id}/stream",
        headers={"X-Integral-Scope": f"ws:{workspace_id}"},
    )
    assert response.status_code == 404
    replay.assert_not_awaited()


@pytest.mark.asyncio
async def test_stop_native_thread_uses_active_durable_work(
    authenticated_client: AsyncClient, test_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.models.nodes import ChatThread

    workspace_id, thread_id = await _owned_native_thread(authenticated_client)
    thread = await ChatThread.get(thread_id)
    thread.provider_id = "integral_native"
    thread.active_work_item_id = "chat-turn:stop-thread"
    await thread.save()
    _install_work_item(
        monkeypatch,
        work_item_id=thread.active_work_item_id,
        principal_id=test_user.user_id,
        workspace_id=workspace_id,
        thread_id=thread_id,
    )
    cancel = AsyncMock(
        return_value=SimpleNamespace(
            work_item_id=thread.active_work_item_id,
            status="cancelled",
            cancel_requested_at="2026-10-08T12:00:00+00:00",
        )
    )
    monkeypatch.setattr("app.agentive.services.work_items.cancel_work_item", cancel)
    monkeypatch.setattr("app.services.change_event.emit_change_event", AsyncMock())
    legacy_cancel = AsyncMock()
    monkeypatch.setattr("app.services.chat_turn_registry.cancel_turn", legacy_cancel)
    response = await authenticated_client.post(
        f"/api/chat/threads/{thread_id}/cancel",
        headers={"X-Integral-Scope": f"ws:{workspace_id}"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["work_item_id"] == thread.active_work_item_id
    assert response.json()["status"] == "cancelled"
    cancel.assert_awaited_once_with(thread.active_work_item_id)
    legacy_cancel.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("input_kind", ["text", "file", "host", "staging"])
async def test_native_message_route_accepts_durable_work_without_inline_run(
    authenticated_client: AsyncClient,
    test_user,
    monkeypatch: pytest.MonkeyPatch,
    input_kind,
) -> None:
    from fastapi.responses import StreamingResponse

    from app.api import ai_chat
    from app.models.nodes import ChatThread
    from app.schemas.agentive.work import ChatTurnSubmissionReceipt

    workspace_id, thread_id = await _owned_native_thread(authenticated_client)
    thread = await ChatThread.get(thread_id)
    thread.provider_id = "integral_native"
    await thread.save()
    monkeypatch.setattr(ai_chat.settings, "INTEGRAL_NATIVE_DURABLE_CHAT_ENABLED", True)
    provider = SimpleNamespace(id="integral_native", is_available=lambda: True)
    monkeypatch.setattr(
        ai_chat, "get_registry", lambda: SimpleNamespace(get=lambda _id: provider)
    )
    monkeypatch.setattr(ai_chat, "_pending_staged_for_turn", AsyncMock(return_value=[]))
    monkeypatch.setattr(ai_chat, "peek_open_batch", lambda *_args: None)
    monkeypatch.setattr(
        ai_chat.chat_store,
        "design_chat_affirmed_for_build",
        AsyncMock(return_value=False),
    )
    acquire = AsyncMock()
    monkeypatch.setattr(ai_chat.chat_turn_registry, "acquire_turn", acquire)
    append = AsyncMock()
    monkeypatch.setattr(ai_chat.chat_store, "append_message", append)
    submit = AsyncMock(
        return_value=ChatTurnSubmissionReceipt(
            client_request_id="durable-route",
            message_id="n.ChatMessage.accepted",
            work_item_id="chat-turn:accepted",
            status="queued",
        )
    )
    monkeypatch.setattr("app.services.chat_turn_submissions.submit_chat_turn", submit)

    async def saved_stream(*_args):
        return StreamingResponse(
            iter([b'event: turn-settled\ndata: {"status":"succeeded"}\n\n']),
            media_type="text/event-stream",
        )

    replay = AsyncMock(side_effect=saved_stream)
    monkeypatch.setattr(ai_chat, "stream_chat_turn_events", replay)
    payload = {"text": "Help me assess my idea", "client_request_id": "durable-route"}
    if input_kind == "file":
        from app.models.edges import HAS_ATTACHMENT
        from app.models.nodes import Attachment

        attachment = await Attachment.create(
            filename="idea-brief.txt",
            mime_type="text/plain",
            size=12,
            storage_key="chat/idea-brief",
            content_hash="a" * 64,
            uploaded_by=test_user.user_id,
            owner_kind="chat",
            scan_status="skipped",
        )
        await thread.connect(attachment, edge=HAS_ATTACHMENT)
        payload = {
            "attachment_ids": [attachment.id],
            "client_request_id": "durable-route",
        }
    if input_kind == "host":
        thread.prompt_queue = {
            "status": "closed",
            "closed_at": "2026-10-08T12:00:00Z",
            "items": [
                {"id": "decision-1", "kind": "staged_write", "status": "rejected"}
            ],
        }
        await thread.save()
        payload = {
            "host_action": "prompt_sheet_resume",
            "client_request_id": "durable-route",
        }
    if input_kind == "staging":
        from datetime import datetime, timedelta, timezone

        from app.agentive.staging import StagedChange
        from app.schemas.agentive.work import ChatTurnHostControl

        now = datetime.now(timezone.utc)
        staged = StagedChange(
            token="test-approved-token",
            user_id=test_user.user_id,
            session_id=thread_id,
            kind="update_entry",
            summary="Update a record",
            diff_human="",
            diff_machine={},
            payload={},
            created_at=now,
            expires_at=now + timedelta(minutes=5),
            state="blessed",
        )
        monkeypatch.setattr(
            ai_chat, "_pending_staged_for_turn", AsyncMock(return_value=[staged])
        )
        execute = AsyncMock(return_value={"consumed": True})
        monkeypatch.setattr(
            "app.agentive.services.staging_apply.execute_blessed_change", execute
        )
        monkeypatch.setattr(
            "app.services.chat_turn_host_controls.capture_chat_host_control",
            AsyncMock(
                return_value=ChatTurnHostControl(
                    action="staging_follow_through",
                    source_digest="a" * 64,
                )
            ),
        )
        payload = {
            "host_action": "staging_follow_through",
            "client_request_id": "durable-route",
        }
    response = await authenticated_client.post(
        f"/api/chat/threads/{thread_id}/messages",
        headers={"X-Integral-Scope": f"ws:{workspace_id}"},
        json=payload,
    )
    assert response.status_code == 200, response.text
    assert response.headers["X-Integral-Work-Item"] == "chat-turn:accepted"
    assert response.headers["X-Integral-Accepted-Message"] == "n.ChatMessage.accepted"
    accepted = submit.await_args.args[0]
    if input_kind == "file":
        assert accepted.parts[0]["type"] == "file"
        assert all(part["type"] != "text" for part in accepted.parts)
        assert (
            accepted.execution_context.attachment_bindings[0]["content_hash"]
            == "a" * 64
        )
        assert (
            "BEGIN_CONTEXT_DATA kind=uploaded_file_references"
            in accepted.execution_context.system_context
        )
    elif input_kind == "host":
        assert accepted.execution_context.host_control.action == "prompt_sheet_resume"
        assert accepted.execution_context.host_control.read_only is True
        assert accepted.parts == [
            {"type": "text", "text": "Approval outcome received."}
        ]
        assert (
            "staging_outcome_continuation" not in accepted.execution_context.extra_data
        )
    elif input_kind == "staging":
        execute.assert_not_awaited()
        assert (
            accepted.execution_context.host_control.action == "staging_follow_through"
        )
        assert staged.state == "blessed"
    else:
        assert accepted.parts == [{"type": "text", "text": "Help me assess my idea"}]
    assert accepted.principal_id == test_user.user_id
    assert accepted.workspace_id == workspace_id
    assert accepted.thread_id == thread_id
    assert len(accepted.client_payload_digest) == 64
    assert "pending_approval_tokens" not in accepted.execution_context.extra_data
    acquire.assert_not_awaited()
    append.assert_not_awaited()
    replay.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"text": "hello"},
        {
            "text": "hello",
            "client_request_id": "files",
            "attachment_ids": ["n.Attachment.file"],
        },
        {
            "text": "hello",
            "client_request_id": "control",
            "host_action": "staging_follow_through",
        },
    ],
)
async def test_durable_message_rejects_unqualified_input_before_acceptance(
    authenticated_client: AsyncClient,
    test_user,
    monkeypatch: pytest.MonkeyPatch,
    payload,
) -> None:
    from app.api import ai_chat
    from app.models.nodes import ChatThread

    workspace_id, thread_id = await _owned_native_thread(authenticated_client)
    thread = await ChatThread.get(thread_id)
    thread.provider_id = "integral_native"
    await thread.save()
    monkeypatch.setattr(ai_chat.settings, "INTEGRAL_NATIVE_DURABLE_CHAT_ENABLED", True)
    submit = AsyncMock()
    monkeypatch.setattr("app.services.chat_turn_submissions.submit_chat_turn", submit)
    response = await authenticated_client.post(
        f"/api/chat/threads/{thread_id}/messages",
        headers={"X-Integral-Scope": f"ws:{workspace_id}"},
        json=payload,
    )
    assert response.status_code == 400, response.text
    submit.assert_not_awaited()
