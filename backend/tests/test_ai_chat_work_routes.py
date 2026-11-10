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
        id=f"o.WorkItem.{work_item_id.removeprefix('o.WorkItem.')}",
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


async def _run_mandate_recovery_worker(work_id, *, mode, expected_exit):
    import asyncio
    import os
    import sys

    from app.config import settings

    script = """
import asyncio, os, sys
from jvspatial.core.context import GraphContext, set_default_context
from jvspatial.db.factory import create_database
from app.agentive.harness.contracts import ResolvedModelRoute
from app.agentive.services import work_budgets, work_mandate_entry_execution
from app.agentive.services import work_mandate_inference, work_recovery, work_worker
from tests.contract.test_work_budgets_postgres import request as budget_request

async def main():
    db = create_database(db_type="postgres")
    set_default_context(GraphContext(database=db))
    work_id, mode = sys.argv[1:]

    async def route(*, reviewed_routes, **_):
        return ResolvedModelRoute(**reviewed_routes[0].model_dump())

    async def infer(context, revision, **_):
        if mode == "recover":
            os._exit(79)
        if mode == "claim":
            os._exit(77)
        request = budget_request(
            revision.model_dump(mode="json"), effect=context.effect_key, cost="0.10"
        )
        held = await work_budgets.reserve_mandate_budget(
            work_item_id=work_id,
            principal_id=context.principal_id,
            workspace_id=context.workspace_id,
            thread_id=context.thread_id,
            request=request,
            execution_context=context,
        )
        marked = await work_budgets.mark_mandate_dispatch_intent(
            reservation_id=held.reservation_id,
            execution_context=context,
            request_fingerprint=request.fingerprint(),
            dispatch_ref="interrupted-provider-request",
        )
        assert marked.dispatch_state == "intent"
        os._exit(77)

    work_mandate_entry_execution.resolve_reviewed_conversation_route = route
    work_mandate_inference.run_reviewed_mandate_inference = infer
    try:
        if mode in {"crash", "claim"}:
            result = await work_worker.process_one_due_item(
                worker_id="crashed-mandate", work_item_id=work_id
            )
            raise AssertionError(f"Mandate did not reach durable dispatch intent: {result}")
        report = await work_recovery.run_recovery_pass(
            reclaim_worker_id="replacement-mandate"
        )
        assert report.reclaimed == 1, report
    finally:
        await db.close()

asyncio.run(main())
"""
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        work_id,
        mode,
        env={
            **os.environ,
            "EMBEDDING_STORE_DRIVER": "null",
            "SECRET_KEY": settings.SECRET_KEY,
        },
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(child.communicate(), timeout=60)
    except TimeoutError:
        child.kill()
        await child.wait()
        raise
    assert child.returncode == expected_exit, (stdout.decode(), stderr.decode())


pytestmark = pytest.mark.usefixtures("standalone_chat_provider")


@pytest.mark.asyncio
async def test_legacy_applied_host_receipt_forces_operational_context_even_with_thanks(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No pending token remains after HTTP apply; the receipt still needs readback."""
    from app.api import ai_chat
    from app.models.nodes import ChatThread

    workspace_id, thread_id = await _owned_native_thread(authenticated_client)
    thread = await ChatThread.get(thread_id)
    thread.provider_id = "integral_native"
    await thread.save()
    monkeypatch.setattr(ai_chat.settings, "INTEGRAL_NATIVE_DURABLE_CHAT_ENABLED", False)
    seen = {}

    class NativeDouble:
        id = "integral_native"
        label = "Integral AI"
        capabilities = {
            "reasoning": False,
            "tools": True,
            "attachments": True,
            "vision": False,
            "voice": False,
        }

        def is_available(self):
            return True

        async def stream_turn(self, ctx):
            seen["context"] = ctx
            yield {"type": "text-delta", "delta": "Verified host receipt"}
            yield {"type": "message-finish"}

    provider = NativeDouble()
    monkeypatch.setattr(
        ai_chat, "get_registry", lambda: SimpleNamespace(get=lambda _id: provider)
    )
    staged = SimpleNamespace(
        kind="update_entry",
        state="blessed",
        token="host-token",
        summary="Update existing record",
    )
    monkeypatch.setattr(
        ai_chat, "_pending_staged_for_turn", AsyncMock(return_value=[staged])
    )
    monkeypatch.setattr(ai_chat, "peek_open_batch", lambda *_args: None)
    execute = AsyncMock(return_value={"consumed": True})
    monkeypatch.setattr(
        "app.agentive.services.staging_apply.execute_blessed_change", execute
    )
    response = await authenticated_client.post(
        f"/api/chat/threads/{thread_id}/messages",
        headers={"X-Integral-Scope": f"ws:{workspace_id}"},
        json={"text": "thanks", "host_action": "staging_follow_through"},
    )
    assert response.status_code == 200, response.text
    execute.assert_awaited_once()
    assert seen["context"].text == "thanks"
    assert seen["context"].extra_data["staging_outcome_continuation"] is True
    assert "host_applied_staging" in seen["context"].system_context


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
    assert response.text.startswith("event: turn-accepted\n")
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


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("past_deadline", "has_intent"),
    [
        pytest.param(False, True, id="intent-before-deadline"),
        pytest.param(True, True, id="intent-after-deadline"),
        pytest.param(True, False, id="no-intent-after-deadline"),
    ],
)
async def test_owned_mandate_review_projects_verified_terms(
    authenticated_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    past_deadline: bool,
    has_intent: bool,
):
    import asyncio
    from uuid import uuid4

    from app.agentive.harness import model_routing
    from app.agentive.harness.contracts import ResolvedModelRoute
    from app.agentive.services.execution_runs import build_core_capability_snapshot
    from app.agentive.services.work_approvals import work_approval_object_id
    from app.agentive.services.work_outbox import _active_database, _is_postgres_txn_db
    from app.models.nodes import ChatThread
    from app.schemas.agentive.work_mandate import WorkMandateRevision
    from app.services import host_hooks
    from tests.test_work_mandate_contract import mandate_payload

    db = _active_database()
    if not _is_postgres_txn_db(db):
        pytest.skip("Requires PostgreSQL")
    workspace_id, thread_id = await _owned_native_thread(authenticated_client)
    thread = await ChatThread.find_one({"id": thread_id})
    assert thread is not None
    headers = {"X-Integral-Scope": f"ws:{workspace_id}"}
    track = await authenticated_client.post(
        "/api/tracks", headers=headers, json={"title": "Mandate evidence"}
    )
    assert track.status_code == 200, track.text
    entry = await authenticated_client.post(
        "/api/entries",
        headers=headers,
        json={
            "track_id": track.json()["track"]["id"],
            "title": "Exact reviewed source",
        },
    )
    assert entry.status_code == 200, entry.text
    entry_id = entry.json()["entry"]["id"]
    source = {"kind": "entry", "resource_id": entry_id}
    payload = mandate_payload()
    payload.update(
        mandate_id=uuid4().hex,
        principal_id=thread.user_id,
        workspace_id=workspace_id,
        thread_id=thread_id,
        inputs=[source],
        grants=[
            {
                "capability_key": "integral_resolve_entry",
                "capability_version": build_core_capability_snapshot()[
                    "manifest_version"
                ],
                "operation": "read",
                "resources": [source],
                "max_calls": 1,
            }
        ],
    )
    payload["model_routes"][0]["credential_source"] = "local"
    revision = WorkMandateRevision.model_validate(payload)
    route = ResolvedModelRoute(**revision.model_routes[0].model_dump())
    monkeypatch.setattr(host_hooks, "get_model_bounds_resolver", lambda: object())
    monkeypatch.setattr(host_hooks, "get_model_price_resolver", lambda: object())
    monkeypatch.setattr(
        model_routing,
        "conversation_turn_routes",
        AsyncMock(return_value=model_routing.TurnModelRoutes(route, route)),
    )
    await db.find_one_and_update(
        "node", {"id": thread_id}, {"$set": {"context.provider_id": "integral_native"}}
    )
    terms = revision.model_dump(
        mode="json",
        exclude={
            "schema_version",
            "assignment_source",
            "entry_source_revisions",
            "principal_id",
            "workspace_id",
            "thread_id",
        },
    )
    pinned_sources = [
        {
            "entry_id": entry_id,
            "record_revision": entry.json()["entry"]["record_revision"],
        }
    ]
    revision = WorkMandateRevision.model_validate(
        {**payload, "entry_source_revisions": pinned_sources}
    )
    proposal_path = f"/api/chat/threads/{thread_id}/mandate-reviews"
    spoofed = await authenticated_client.post(
        proposal_path, headers=headers, json={**terms, "principal_id": "other"}
    )
    assert spoofed.status_code == 400, spoofed.text
    proposals = await asyncio.gather(
        *(
            authenticated_client.post(proposal_path, headers=headers, json=terms)
            for _ in range(2)
        )
    )
    for proposal in proposals:
        assert proposal.status_code == 200, proposal.text
    assert proposals[0].json() == proposals[1].json()
    changed = await authenticated_client.post(
        proposal_path, headers=headers, json={**terms, "goal": "Changed goal"}
    )
    assert changed.status_code == 409, changed.text
    work_item_id = proposals[0].json()["work_item_id"]
    listed = await authenticated_client.get(proposal_path, headers=headers)
    assert listed.status_code == 200, listed.text
    assert listed.json()["reviews"] == [
        {
            "work_item_id": work_item_id,
            "goal": terms["goal"],
            "work_status": "waiting_for_human",
        }
    ]
    item = await WorkItem.find_one({"work_item_id": work_item_id})
    assert item is not None
    approval_id = work_approval_object_id(item.plan["mandate_approval_id"])
    path = f"/api/chat/threads/{thread_id}/work-items/{work_item_id}/mandate-review"
    response = await authenticated_client.get(path, headers=headers)
    assert response.status_code == 200, response.text
    review = response.json()
    assert review["review_digest"] == revision.review_digest()
    assert review["terms"]["entry_source_revisions"] == pinned_sources
    assert review["approval_status"] == "pending"
    assert review["entry_read_preflight"] == "current"
    assert review["model_route_preflight"] == "current"
    stale_capability = await authenticated_client.post(
        proposal_path,
        headers=headers,
        json={
            **terms,
            "revision": 2,
            "grants": [{**terms["grants"][0], "capability_version": "obsolete"}],
        },
    )
    assert stale_capability.status_code == 200, stale_capability.text
    assert stale_capability.json()["entry_read_preflight"] == "denied"
    denied_review = stale_capability.json()
    denied_path = (
        f"/api/chat/threads/{thread_id}/work-items/{denied_review['work_item_id']}"
        "/mandate-review/approve"
    )
    denied = await authenticated_client.post(
        denied_path,
        headers=headers,
        json={"review_digest": denied_review["review_digest"]},
    )
    assert denied.status_code == 409, denied.text

    edited = await authenticated_client.put(
        f"/api/entries/{entry_id}", headers=headers, json={"title": "New evidence"}
    )
    assert edited.status_code == 200, edited.text
    stale_evidence = await authenticated_client.post(
        f"{path}/approve",
        headers=headers,
        json={"review_digest": review["review_digest"]},
    )
    assert stale_evidence.status_code == 409, stale_evidence.text
    old_review = await authenticated_client.get(path, headers=headers)
    assert old_review.json()["entry_read_preflight"] == "denied"
    assert old_review.json()["terms"]["entry_source_revisions"] == pinned_sources

    positive = await authenticated_client.post(
        proposal_path, headers=headers, json={**terms, "revision": 3}
    )
    assert positive.status_code == 200, positive.text
    positive_review = positive.json()
    assert positive_review["terms"]["entry_source_revisions"] == [
        {
            "entry_id": entry_id,
            "record_revision": edited.json()["entry"]["record_revision"],
        }
    ]
    positive_id = positive_review["work_item_id"]
    positive_path = (
        f"/api/chat/threads/{thread_id}/work-items/{positive_id}/mandate-review"
    )

    async def approve():
        return await authenticated_client.post(
            f"{positive_path}/approve",
            headers=headers,
            json={"review_digest": positive_review["review_digest"]},
        )

    for approved in await asyncio.gather(approve(), approve()):
        assert approved.status_code == 200, approved.text
        assert approved.json() == {
            "approval_status": "approved",
            "work_status": "waiting_for_human",
        }
    approved_review = await authenticated_client.get(positive_path, headers=headers)
    assert approved_review.json()["approval_status"] == "approved"
    from app.agentive.services import work_items

    assert (
        await work_items.claim_due_candidate(
            worker_id="must-stay-parked", work_item_id=positive_id, lease_seconds=30
        )
        is None
    )
    (approval_event,) = await db.find(
        "object",
        {
            "entity": "WorkOutboxEntry",
            "context.work_item_id": positive_id,
            "context.topic": "work.mandate_review_approved",
        },
    )
    assert (
        approval_event["context"]["payload"]["review_digest"]
        == positive_review["review_digest"]
    )

    from app.agentive.services import (
        work_mandate_inference,
        work_worker,
    )
    from app.config import settings

    async def infer(context, reviewed, *, prompt, resolve_route, **_):
        assert context.thread_id == thread_id
        assert await resolve_route() == route
        assert prompt["evidence"] == [
            {
                **reviewed.entry_source_revisions[0].model_dump(),
                "title": "New evidence",
                "body": "",
            }
        ]
        return "Private reviewed answer"

    monkeypatch.setattr(work_mandate_inference, "run_reviewed_mandate_inference", infer)

    async def request_run(work_id, digest):
        return await authenticated_client.post(
            f"/api/chat/threads/{thread_id}/work-items/{work_id}/mandate-review/run",
            headers=headers,
            json={"review_digest": digest},
        )

    monkeypatch.setattr(settings, "INTEGRAL_MANDATE_ENTRY_EXECUTION_ENABLED", False)
    disabled = await request_run(positive_id, positive_review["review_digest"])
    assert disabled.status_code == 503, disabled.text
    monkeypatch.setattr(settings, "INTEGRAL_MANDATE_ENTRY_EXECUTION_ENABLED", True)
    form_terms = {
        "mandate_id": uuid4().hex,
        "goal": "Summarize the reviewed Entry",
        "entry_ids": [entry_id, entry_id],
        "deadline_at": terms["limits"]["deadline_at"],
        "max_spend": "0.25",
    }
    from_form = await authenticated_client.post(
        f"{proposal_path}/entry-read", headers=headers, json=form_terms
    )
    assert from_form.status_code == 200, from_form.text
    form_review = from_form.json()
    assert form_review["approval_status"] == "pending"
    assert form_review["entry_read_preflight"] == "current"
    assert form_review["terms"]["limits"]["max_model_requests"] == 1
    assert form_review["terms"]["limits"]["max_tool_calls"] == 1
    assert form_review["terms"]["limits"]["max_spend"] == "0.25"
    assert "credential_ref" not in from_form.text
    unapproved = await request_run(work_item_id, review["review_digest"])
    assert unapproved.status_code == 409, unapproved.text
    stale_run = await request_run(positive_id, "0" * 64)
    assert stale_run.status_code == 409, stale_run.text

    async def run_admitted(work_id, digest):
        for admitted in await asyncio.gather(
            request_run(work_id, digest), request_run(work_id, digest)
        ):
            assert admitted.status_code == 200, admitted.text
            assert admitted.json() == {"work_item_id": work_id, "work_status": "queued"}
        return await work_worker.process_one_due_item(
            worker_id="reviewed-entry-worker", work_item_id=work_id
        )

    settled = await run_admitted(positive_id, positive_review["review_digest"])
    assert settled is not None and settled.status == "succeeded", settled
    assert len(settled.result_refs) == 1
    transcript = await authenticated_client.get(
        f"/api/chat/threads/{thread_id}", headers=headers
    )
    result = [
        m for m in transcript.json()["messages"] if m["id"] in settled.result_refs
    ]
    assert result[0]["parts"] == [{"type": "text", "text": "Private reviewed answer"}]
    assert result[0]["provider_metadata"]["mandate_entry_read_result"] is True

    interrupted_id = form_review["work_item_id"]
    interrupted_path = (
        f"/api/chat/threads/{thread_id}/work-items/{interrupted_id}/mandate-review"
    )
    approved_interruption = await authenticated_client.post(
        f"{interrupted_path}/approve",
        headers=headers,
        json={"review_digest": form_review["review_digest"]},
    )
    assert approved_interruption.status_code == 200, approved_interruption.text
    queued_interruption = await request_run(
        interrupted_id, form_review["review_digest"]
    )
    assert queued_interruption.status_code == 200, queued_interruption.text
    await _run_mandate_recovery_worker(
        interrupted_id, mode="crash" if has_intent else "claim", expected_exit=77
    )
    holds = await db.find(
        "object",
        {
            "entity": "WorkBudgetReservation",
            "context.root_work_item_id": interrupted_id,
        },
    )
    assert len(holds) == int(has_intent)
    if has_intent:
        assert holds[0]["context"]["dispatch_state"] == "intent"
    past = "2000-01-01T00:00:00+00:00"
    expiry = {"context.lease_expires_at": past}
    if past_deadline:
        expiry["context.deadline_at"] = past
    running = await db.find_one_and_update(
        "object",
        {"id": f"o.WorkItem.{interrupted_id}", "context.status": "running"},
        {"$set": expiry},
    )
    assert running is not None
    await _run_mandate_recovery_worker(interrupted_id, mode="recover", expected_exit=0)
    reconciled = await authenticated_client.get(interrupted_path, headers=headers)
    assert reconciled.status_code == 200, reconciled.text
    assert reconciled.json()["requires_reconciliation"] is has_intent
    assert reconciled.json()["work_status"] == ("failed" if has_intent else "expired")
    accounting = reconciled.json()["accounting"]
    if has_intent:
        assert accounting == {
            "status": "awaiting_receipt",
            "reserved_usd": "0.1",
            "charged_usd": None,
        }
    else:
        assert accounting == {"status": "not_recorded"}
    recovered = (await db.get("object", f"o.WorkItem.{interrupted_id}"))["context"]
    assert recovered["attempt"] == running["context"]["attempt"] == 1
    assert recovered["lease_fence"] == running["context"]["lease_fence"] + 1
    assert not recovered["result_refs"]
    if has_intent:
        retained = (await db.get("object", holds[0]["id"]))["context"]
        assert (
            retained["status"] == "reserved" and retained["dispatch_state"] == "intent"
        )

    changed_during_run = await authenticated_client.post(
        proposal_path, headers=headers, json={**terms, "revision": 4}
    )
    assert changed_during_run.status_code == 200, changed_during_run.text
    changing_review = changed_during_run.json()
    changing_id = changing_review["work_item_id"]
    response = await authenticated_client.post(
        f"/api/chat/threads/{thread_id}/work-items/{changing_id}/mandate-review/approve",
        headers=headers,
        json={"review_digest": changing_review["review_digest"]},
    )
    assert response.status_code == 200, response.text

    async def infer_with_changed_source(*args, **kwargs):
        answer = await infer(*args, **kwargs)
        changed_entry = await authenticated_client.put(
            f"/api/entries/{entry_id}",
            headers=headers,
            json={"title": "Changed during inference"},
        )
        assert changed_entry.status_code == 200, changed_entry.text
        return answer

    monkeypatch.setattr(
        work_mandate_inference,
        "run_reviewed_mandate_inference",
        infer_with_changed_source,
    )
    refused = await run_admitted(changing_id, changing_review["review_digest"])
    assert refused is not None and refused.status == "failed", refused
    assert refused.failure["code"] == "work.mandate_grants_unavailable"
    assert not refused.result_refs

    deleted = await authenticated_client.delete(
        f"/api/entries/{entry_id}", headers=headers
    )
    assert deleted.status_code == 200, deleted.text
    unavailable_proposal = await authenticated_client.post(
        f"{proposal_path}/entry-read",
        headers=headers,
        json={**form_terms, "mandate_id": uuid4().hex},
    )
    assert unavailable_proposal.status_code == 409, unavailable_proposal.text
    unavailable = await authenticated_client.get(path, headers=headers)
    assert unavailable.status_code == 200, unavailable.text
    assert unavailable.json()["entry_read_preflight"] == "denied"
    assert review["work_status"] == "waiting_for_human"
    assert review["terms"]["goal"] == revision.goal
    assert "credential_ref" not in response.text

    other_thread = await authenticated_client.post(
        "/api/chat/threads",
        headers=headers,
        json={"provider_id": "test-provider", "agent_id": "aiva"},
    )
    assert other_thread.status_code == 200, other_thread.text
    wrong_thread = await authenticated_client.get(
        path.replace(thread_id, other_thread.json()["id"]), headers=headers
    )
    assert wrong_thread.status_code == 404

    await db.find_one_and_update(
        "object",
        {"id": approval_id},
        {"$set": {"context.authority_digest": "wrong"}},
    )
    tampered = await authenticated_client.get(path, headers=headers)
    assert tampered.status_code == 404

    await db.find_one_and_update(
        "object",
        {"id": approval_id},
        {"$set": {"context.authority_digest": revision.review_digest()}},
    )
    reject_path = f"{path}/reject"
    stale = await authenticated_client.post(
        reject_path, headers=headers, json={"review_digest": "0" * 64}
    )
    assert stale.status_code == 409, stale.text
    still_pending = await authenticated_client.get(path, headers=headers)
    assert still_pending.json()["approval_status"] == "pending"

    async def reject():
        return await authenticated_client.post(
            reject_path,
            headers=headers,
            json={"review_digest": revision.review_digest()},
        )

    for rejected in await asyncio.gather(reject(), reject()):
        assert rejected.status_code == 200, rejected.text
        assert rejected.json() == {
            "approval_status": "rejected",
            "work_status": "failed",
        }
    stored = await authenticated_client.get(path, headers=headers)
    assert stored.json()["approval_status"] == "rejected"
    row = await db.get("object", item.id)
    assert row["context"]["failure"]["code"] == "work.approval_rejected"
    assert row["context"]["transition_seq"] == 1
    (event,) = await db.find(
        "object",
        {
            "entity": "WorkOutboxEntry",
            "context.work_item_id": item.work_item_id,
            "context.topic": "work.transitioned",
        },
    )
    assert event["context"]["payload"]["decision"] == "rejected"


@pytest.mark.asyncio
async def test_conditional_thread_read_returns_only_changed_transcript(
    authenticated_client: AsyncClient,
):
    from app.services.chat_threads import append_message, get_thread

    workspace_id, thread_id = await _owned_native_thread(authenticated_client)
    path = f"/api/chat/threads/{thread_id}"
    headers = {"X-Integral-Scope": f"ws:{workspace_id}"}
    initial = await authenticated_client.get(path, headers=headers)
    assert initial.status_code == 200, initial.text
    params = {"known_revision": initial.json()["updated_at"]}
    unchanged = await authenticated_client.get(path, headers=headers, params=params)
    assert unchanged.status_code == 200, unchanged.text
    assert unchanged.json() == {"unchanged": True}

    thread = await get_thread(thread_id)
    saved = await append_message(
        thread=thread, role="assistant", parts=[{"type": "text", "text": "Watch alert"}]
    )
    changed = await authenticated_client.get(path, headers=headers, params=params)
    assert changed.status_code == 200, changed.text
    assert [message["id"] for message in changed.json()["messages"]] == [saved.id]
    assert changed.json()["updated_at"] != params["known_revision"]

    other = await authenticated_client.post("/api/workspaces", json={"name": "Other"})
    assert other.status_code == 200, other.text
    other_id = other.json()["workspace"]["id"]
    mismatch = await authenticated_client.get(
        path,
        headers={"X-Integral-Scope": f"ws:{other_id}"},
        params={"known_revision": changed.json()["updated_at"]},
    )
    assert mismatch.status_code == 409, mismatch.text


@pytest.mark.asyncio
async def test_activity_reports_registry_cleanup_before_releasing_composer(monkeypatch):
    from app.api import ai_chat

    thread = SimpleNamespace(
        id="thread", provider_id="test-provider", active_work_item_id=""
    )
    monkeypatch.setattr(ai_chat, "_resolve_principal", lambda request: ("owner", None))
    monkeypatch.setattr(
        ai_chat, "_resolve_owned_thread", AsyncMock(return_value=thread)
    )
    monkeypatch.setattr(
        ai_chat.chat_turn_registry, "get_turn", lambda _: SimpleNamespace(turn_id="old")
    )
    assert await ai_chat.thread_activity(None, "thread") == {
        "running": True,
        "work_item_id": None,
        "turn_id": "old",
    }
    monkeypatch.setattr(ai_chat.chat_turn_registry, "get_turn", lambda _: None)
    assert (await ai_chat.thread_activity(None, "thread"))["running"] is False


@pytest.mark.asyncio
async def test_activity_requires_owned_thread_before_reading_turn(monkeypatch):
    from app.api import ai_chat
    from app.api.errors import ResourceNotFoundError

    monkeypatch.setattr(
        ai_chat, "_resolve_principal", lambda request: ("outsider", None)
    )
    monkeypatch.setattr(
        ai_chat,
        "_resolve_owned_thread",
        AsyncMock(side_effect=ResourceNotFoundError(message="Chat thread not found")),
    )
    with pytest.raises(ResourceNotFoundError):
        await ai_chat.thread_activity(None, "private-thread")


@pytest.mark.asyncio
async def test_conditional_cancel_never_targets_a_newer_work_item(monkeypatch):
    from app.api import ai_chat

    monkeypatch.setattr(ai_chat, "_resolve_principal", lambda request: ("owner", None))
    monkeypatch.setattr(
        ai_chat,
        "_resolve_owned_thread",
        AsyncMock(
            return_value=SimpleNamespace(
                id="thread",
                provider_id="integral_native",
                active_work_item_id="new-work",
            )
        ),
    )
    monkeypatch.setattr(ai_chat.chat_turn_registry, "get_turn", lambda _: None)
    cancel = AsyncMock()
    monkeypatch.setattr(ai_chat, "cancel_chat_turn", cancel)
    result = await ai_chat._cancel_observed_thread(
        None, "thread", expected_work_item_id="old-work"
    )
    assert result["cancelled"] is False
    cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_conditional_cancel_never_targets_a_newer_legacy_turn(monkeypatch):
    from app.api import ai_chat

    monkeypatch.setattr(ai_chat, "_resolve_principal", lambda request: ("owner", None))
    monkeypatch.setattr(
        ai_chat,
        "_resolve_owned_thread",
        AsyncMock(
            return_value=SimpleNamespace(
                id="thread", provider_id="test-provider", active_work_item_id=""
            )
        ),
    )
    monkeypatch.setattr(
        ai_chat.chat_turn_registry, "get_turn", lambda _: SimpleNamespace(turn_id="new")
    )
    cancel = AsyncMock()
    monkeypatch.setattr(ai_chat.chat_turn_registry, "cancel_turn", cancel)
    result = await ai_chat._cancel_observed_thread(
        None, "thread", expected_turn_id="old"
    )
    assert result["cancelled"] is False
    cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancellation_activity_uses_fresh_thread_and_work_reads(monkeypatch):
    from app.api import ai_chat
    from app.models.nodes import ChatThread

    thread = SimpleNamespace(
        id="thread",
        user_id="owner",
        provider_id="integral_native",
        active_work_item_id="work",
    )
    thread_read = AsyncMock(return_value=thread)
    monkeypatch.setattr(ChatThread, "find_one", thread_read)
    monkeypatch.setattr(ai_chat, "_resolve_principal", lambda _: ("owner", None))
    monkeypatch.setattr(
        ai_chat, "_resolve_turn_workspace", AsyncMock(return_value="workspace")
    )
    monkeypatch.setattr(ai_chat.chat_turn_registry, "get_turn", lambda _: None)
    item = SimpleNamespace(
        id="o.WorkItem.work",
        work_item_id="work",
        kind="chat_turn",
        principal_id="owner",
        workspace_id="workspace",
        thread_id="thread",
        status="cancelled",
    )
    work_read = AsyncMock(return_value=item)
    monkeypatch.setattr(WorkItem, "find_one", work_read)
    cached = AsyncMock(side_effect=AssertionError("Cached WorkItem read"))
    monkeypatch.setattr(WorkItem, "get", cached)
    result = await ai_chat.thread_activity(None, "thread")
    assert result["running"] is False
    thread_read.assert_awaited_once_with({"id": "thread"})
    work_read.assert_awaited_once_with({"context.work_item_id": "work"})
    cached.assert_not_awaited()


@pytest.mark.asyncio
async def test_activity_reads_persisted_work_item_and_releases_terminal_turn(
    authenticated_client: AsyncClient,
) -> None:
    """Exercise Object query mapping against storage, not a mocked find_one."""
    from app.models.nodes import ChatThread

    workspace_id, thread_id = await _owned_native_thread(authenticated_client)
    thread = await ChatThread.get(thread_id)
    assert thread is not None
    item = await WorkItem.create(
        id="o.WorkItem.persisted-activity",
        work_item_id="persisted-activity",
        kind="chat_turn",
        principal_id=thread.user_id,
        workspace_id=workspace_id,
        thread_id=thread_id,
        status="running",
    )
    thread.provider_id = "integral_native"
    thread.active_work_item_id = item.work_item_id
    await thread.save()
    headers = {"X-Integral-Scope": f"ws:{workspace_id}"}
    response = await authenticated_client.get(
        f"/api/chat/threads/{thread_id}/activity", headers=headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["running"] is True
    assert response.json()["work_item_id"] == item.work_item_id
    item.status = "cancelled"
    await item.save()
    response = await authenticated_client.get(
        f"/api/chat/threads/{thread_id}/activity", headers=headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["running"] is False


@pytest.mark.asyncio
async def test_fresh_work_item_refuses_noncanonical_object_identity(monkeypatch):
    from app.api import ai_chat
    from app.api.errors import ResourceNotFoundError

    item = SimpleNamespace(
        id="o.WorkItem.other",
        work_item_id="work",
        kind="chat_turn",
        principal_id="owner",
        workspace_id="workspace",
        thread_id="thread",
    )
    monkeypatch.setattr(WorkItem, "find_one", AsyncMock(return_value=item))
    with pytest.raises(ResourceNotFoundError):
        await ai_chat._resolve_owned_chat_work_item(
            work_item_id="work",
            principal_id="owner",
            workspace_id="workspace",
            thread_id="thread",
            fresh=True,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        b"[]",
        b"null",
        b"bad json",
        b'{"expected_turn_id": 123}',
        b'{"expected_turn_id": ""}',
        b'{"unexpected": "turn"}',
    ],
)
async def test_cancel_rejects_invalid_target_before_dispatch(monkeypatch, body):
    from app.api import ai_chat
    from app.api.errors import BadRequestError

    dispatch = AsyncMock()
    monkeypatch.setattr(ai_chat, "_cancel_observed_thread", dispatch)
    with pytest.raises(BadRequestError):
        await ai_chat.cancel_thread(
            SimpleNamespace(body=AsyncMock(return_value=body)), "thread"
        )
    dispatch.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"", b"{}"])
async def test_cancel_accepts_legacy_empty_target(monkeypatch, body):
    from app.api import ai_chat

    dispatch = AsyncMock(return_value={"cancelled": False})
    monkeypatch.setattr(ai_chat, "_cancel_observed_thread", dispatch)
    request = SimpleNamespace(body=AsyncMock(return_value=body))
    await ai_chat.cancel_thread(request, "thread")
    dispatch.assert_awaited_once_with(
        request, "thread", expected_work_item_id=None, expected_turn_id=None
    )


@pytest.mark.asyncio
async def test_follow_up_routes_bind_owned_thread_and_validated_content(
    authenticated_client: AsyncClient,
    second_user_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.models.nodes import ChatThread
    from app.schemas.agentive.chat_followups import ChatFollowUpRequest
    from app.services import chat_followups

    workspace_id, thread_id = await _owned_native_thread(authenticated_client)
    thread = await ChatThread.get(thread_id)
    thread.provider_id = "integral_native"
    await thread.save()
    path = f"/api/chat/threads/{thread_id}/follow-ups"
    headers = {"X-Integral-Scope": f"ws:{workspace_id}"}
    follow_up = ChatFollowUpRequest(
        client_request_id="next-1", after_work_item_id="turn-1", text="Follow up."
    )
    payload = follow_up.model_dump(mode="json")
    accept = AsyncMock(return_value="queued")
    read = AsyncMock(return_value=[follow_up])
    dismiss = AsyncMock()
    monkeypatch.setattr(
        chat_followups, "chat_follow_ups_paused", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(chat_followups, "accept_chat_follow_up", accept)
    monkeypatch.setattr(chat_followups, "list_chat_follow_ups", read)
    monkeypatch.setattr(chat_followups, "dismiss_chat_follow_up", dismiss)

    response = await authenticated_client.post(path, json=payload, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json() == {"client_request_id": "next-1", "status": "queued"}
    scope = dict(
        principal_id=thread.user_id, workspace_id=workspace_id, thread_id=thread_id
    )
    accept.assert_awaited_once_with(**scope, request=follow_up)
    response = await authenticated_client.get(path, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json() == {"items": [payload], "paused": False}
    read.assert_awaited_once_with(**scope)
    response = await authenticated_client.delete(f"{path}/next-1", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "dismissed"
    dismiss.assert_awaited_once_with(**scope, client_request_id="next-1")

    invalid = await authenticated_client.post(
        path, json={**payload, "host_control": "forged"}, headers=headers
    )
    assert invalid.status_code == 400
    wrong_workspace = await authenticated_client.post(
        path, json=payload, headers={"X-Integral-Scope": "ws:n.Workspace.wrong"}
    )
    assert wrong_workspace.status_code == 409
    stranger = await second_user_client.get(path, headers=headers)
    assert stranger.status_code == 404
    assert accept.await_count == read.await_count == 1
