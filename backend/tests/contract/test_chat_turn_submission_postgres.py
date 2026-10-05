"""PostgreSQL acceptance guarantees for native chat turn submission."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from jvspatial.core.context import GraphContext, set_default_context

from app.agentive.services import work_items
from app.agentive.services.work_execution import build_work_execution_context
from app.agentive.work_models import WorkItem, WorkOutboxEntry
from app.api.errors import InsufficientPermissionsError, ResourceConflictError
from app.models.edges import CONTAINS, IS_MEMBER_OF
from app.models.harness_records import (
    HarnessTurnAdmissionSlot,
    HarnessTurnInputRecord,
)
from app.models.nodes import ChatMessage, ChatThread
from app.schemas.agentive.work import (
    ChatTurnExecutionContext,
    ChatTurnSubmissionRequest,
    WorkError,
)
from app.services.chat_threads import create_thread
from app.services.chat_turn_admission import release_chat_turn_admission
from app.services.chat_turn_submissions import submit_chat_turn
from app.services.chat_turn_transcript import persist_work_item_assistant_result
from app.services.chat_turn_worker_input import load_claimed_chat_turn_input
from tests.fixtures.workspaces import make_org_workspace


@pytest.fixture
def postgres_graph_context(postgres_raw_db):
    """Bind graph writes to the isolated PostgreSQL test database."""
    from jvspatial.core.context import _default_context_var

    token = set_default_context(GraphContext(database=postgres_raw_db))
    try:
        yield
    finally:
        _default_context_var.reset(token)


async def _submission_context() -> tuple[ChatThread, str, str]:
    from app.services.app_graph import catalog_workspace, ensure_integral_app_graph

    await ensure_integral_app_graph(include_library=False)
    suffix = uuid.uuid4().hex
    workspace = await make_org_workspace(f"chat-submit-{suffix}")
    await catalog_workspace(workspace)
    owners = await workspace.nodes(
        edge=[IS_MEMBER_OF], node=["User"], direction="in", limit=5
    )
    assert owners
    owner = owners[0]
    thread = await create_thread(
        user_id=owner.id,
        workspace_id=workspace.id,
        provider_id="integral_native",
    )
    return thread, owner.id, workspace.id


def _request(
    thread: ChatThread,
    principal_id: str,
    workspace_id: str,
    *,
    request_id: str = "request-1",
    text: str = "confidential prompt text",
    execution_context: ChatTurnExecutionContext | None = None,
) -> ChatTurnSubmissionRequest:
    return ChatTurnSubmissionRequest(
        principal_id=principal_id,
        workspace_id=workspace_id,
        thread_id=thread.id,
        client_request_id=request_id,
        parts=[{"type": "text", "text": text}],
        execution_context=execution_context,
    )


async def _queued_for_thread(
    thread_id: str,
) -> tuple[list[WorkItem], list[WorkOutboxEntry]]:
    items = await WorkItem.find({"thread_id": thread_id})
    work_ids = {item.work_item_id for item in items}
    outbox = [
        entry
        for entry in await WorkOutboxEntry.find({})
        if entry.work_item_id in work_ids
    ]
    return items, outbox


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_submission_retry_is_atomic_private_and_rooted(
    postgres_graph_context,
) -> None:
    thread, owner_id, workspace_id = await _submission_context()
    request = _request(thread, owner_id, workspace_id)

    first = await submit_chat_turn(request)
    retry = await submit_chat_turn(request)

    assert retry == first
    messages = await thread.nodes(
        edge=[CONTAINS], node=["ChatMessage"], direction="out", limit=10
    )
    accepted = [message for message in messages if message.id == first.message_id]
    assert len(accepted) == 1
    items, outbox = await _queued_for_thread(thread.id)
    assert len(items) == 1
    assert len(outbox) == 1
    persisted_thread = await ChatThread.get(thread.id)
    assert persisted_thread is not None
    assert persisted_thread.active_work_item_id == first.work_item_id
    slots = await HarnessTurnAdmissionSlot.find({"work_item_id": first.work_item_id})
    assert len(slots) == 1
    assert items[0].input_payload == {
        "accepted_message_id": first.message_id,
        "request_fingerprint": items[0].input_payload["request_fingerprint"],
    }
    assert "confidential prompt text" not in str(items[0].model_dump())

    from jvspatial.core.entities import Root

    from app.services.graph_reachability import GraphReachabilityWalker

    root = await Root.get(None)
    walker = GraphReachabilityWalker(visited_ids=set(), visited_by_class={})
    await walker.spawn(root)
    assert first.message_id in walker.visited_ids


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_changed_content_conflicts_without_duplicate_records(
    postgres_graph_context,
) -> None:
    thread, owner_id, workspace_id = await _submission_context()
    await submit_chat_turn(_request(thread, owner_id, workspace_id))

    with pytest.raises(ResourceConflictError) as conflict:
        await submit_chat_turn(
            _request(thread, owner_id, workspace_id, text="different prompt")
        )

    assert "confidential" not in str(conflict.value).lower()
    items, _outbox = await _queued_for_thread(thread.id)
    messages = await thread.nodes(
        edge=[CONTAINS], node=["ChatMessage"], direction="out", limit=10
    )
    assert len(items) == 1
    assert len([message for message in messages if message.role == "user"]) == 1


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_submission_rejects_foreign_principal_before_enqueue(
    postgres_graph_context,
) -> None:
    thread, _owner_id, workspace_id = await _submission_context()

    with pytest.raises(InsufficientPermissionsError):
        await submit_chat_turn(_request(thread, "different-principal", workspace_id))

    items, _outbox = await _queued_for_thread(thread.id)
    assert items == []


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_concurrent_duplicate_submission_has_one_winner(
    postgres_graph_context,
) -> None:
    thread, owner_id, workspace_id = await _submission_context()
    request = _request(thread, owner_id, workspace_id)

    first, second = await asyncio.gather(
        submit_chat_turn(request), submit_chat_turn(request)
    )

    assert first == second
    items, outbox = await _queued_for_thread(thread.id)
    messages = await thread.nodes(
        edge=[CONTAINS], node=["ChatMessage"], direction="out", limit=10
    )
    assert len(items) == len(outbox) == 1
    assert len([message for message in messages if message.role == "user"]) == 1


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_distinct_request_on_active_thread_is_rejected(
    postgres_graph_context,
) -> None:
    thread, owner_id, workspace_id = await _submission_context()
    first = await submit_chat_turn(_request(thread, owner_id, workspace_id))

    with pytest.raises(ResourceConflictError) as conflict:
        await submit_chat_turn(
            _request(thread, owner_id, workspace_id, request_id="request-2")
        )

    assert conflict.value.details["reason"] == "thread_busy"
    items, _outbox = await _queued_for_thread(thread.id)
    assert [item.work_item_id for item in items] == [first.work_item_id]
    assert (
        len(await HarnessTurnAdmissionSlot.find({"work_item_id": first.work_item_id}))
        == 1
    )


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_concurrent_distinct_requests_admit_one_per_thread(
    postgres_graph_context,
) -> None:
    thread, owner_id, workspace_id = await _submission_context()
    results = await asyncio.gather(
        submit_chat_turn(_request(thread, owner_id, workspace_id)),
        submit_chat_turn(
            _request(
                thread,
                owner_id,
                workspace_id,
                request_id="request-2",
                text="second prompt",
            )
        ),
        return_exceptions=True,
    )

    receipts = [result for result in results if not isinstance(result, BaseException)]
    errors = [result for result in results if isinstance(result, BaseException)]
    assert len(receipts) == len(errors) == 1
    assert isinstance(errors[0], ResourceConflictError)
    assert errors[0].details["reason"] == "thread_busy"
    items, outbox = await _queued_for_thread(thread.id)
    messages = await thread.nodes(
        edge=[CONTAINS], node=["ChatMessage"], direction="out", limit=10
    )
    assert len(items) == len(outbox) == 1
    assert len([message for message in messages if message.role == "user"]) == 1


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_input_capsule_is_encrypted_scoped_and_idempotent(
    postgres_graph_context,
) -> None:
    from app.agentive.harness.turn_input import load_turn_input_capsule

    thread, owner_id, workspace_id = await _submission_context()
    execution_context = ChatTurnExecutionContext(
        system_context="server-only instruction",
        focused_track_id="n.Track.private",
        extra_data={"run_id": "run-private", "page_context": {"route_path": "/agent"}},
    )
    request = _request(
        thread,
        owner_id,
        workspace_id,
        execution_context=execution_context,
    )
    first = await submit_chat_turn(request)
    retry = await submit_chat_turn(request)

    assert first == retry
    work_item = await WorkItem.get(f"o.WorkItem.{first.work_item_id}")
    assert work_item is not None
    payload = work_item.input_payload
    assert set(payload) == {
        "accepted_message_id",
        "request_fingerprint",
        "capsule_id",
        "capsule_digest",
    }
    assert "confidential prompt text" not in str(payload)
    records = await HarnessTurnInputRecord.find({"work_item_id": first.work_item_id})
    assert len(records) == 1
    capsule = records[0]
    assert capsule.payload_ciphertext.startswith("v1:")
    assert "confidential prompt text" not in capsule.payload_ciphertext
    assert "server-only instruction" not in capsule.payload_ciphertext
    from jvspatial.core.context import (
        GraphContext,
        _default_context_var,
        get_default_context,
        set_default_context,
    )

    reopened_context = GraphContext(database=get_default_context().database)
    token = set_default_context(reopened_context)
    try:
        restored = await load_turn_input_capsule(
            capsule_id=payload["capsule_id"],
            expected_digest=payload["capsule_digest"],
            principal_id=owner_id,
            workspace_id=workspace_id,
            thread_id=thread.id,
            work_item_id=first.work_item_id,
        )
    finally:
        _default_context_var.reset(token)
    assert restored.execution_context == execution_context

    with pytest.raises(ResourceConflictError):
        await submit_chat_turn(
            _request(
                thread,
                owner_id,
                workspace_id,
                execution_context=ChatTurnExecutionContext(
                    system_context="changed server-only instruction",
                    focused_track_id="n.Track.private",
                    extra_data={
                        "run_id": "run-private",
                        "page_context": {"route_path": "/agent"},
                    },
                ),
            )
        )


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_claimed_chat_turn_rebuilds_only_its_scoped_text_input(
    postgres_graph_context,
) -> None:
    """A worker resolves prompt/context from the accepted row and capsule."""
    thread, owner_id, workspace_id = await _submission_context()
    execution_context = ChatTurnExecutionContext(
        system_context="server-only instruction",
        focused_track_id="n.Track.focused",
        extra_data={"page_context": {"route_path": "/agent"}},
    )
    receipt = await submit_chat_turn(
        _request(
            thread,
            owner_id,
            workspace_id,
            execution_context=execution_context,
        )
    )
    claimed = await work_items.claim_due_candidate(
        worker_id="chat-input-contract",
        lease_seconds=60,
        work_item_id=receipt.work_item_id,
    )
    assert claimed is not None

    rebuilt = await load_claimed_chat_turn_input(claimed)

    assert rebuilt.thread.id == thread.id
    assert rebuilt.message.id == receipt.message_id
    assert rebuilt.text == "confidential prompt text"
    assert rebuilt.execution_context == execution_context
    assert rebuilt.user_email == ""


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_turn_input_capsule_rejects_cross_scope_reference(
    postgres_graph_context,
) -> None:
    from app.agentive.harness.turn_input import load_turn_input_capsule

    thread, owner_id, workspace_id = await _submission_context()
    receipt = await submit_chat_turn(
        _request(
            thread,
            owner_id,
            workspace_id,
            execution_context=ChatTurnExecutionContext(system_context="private"),
        )
    )
    work_item = await WorkItem.get(f"o.WorkItem.{receipt.work_item_id}")
    assert work_item is not None

    with pytest.raises(ValueError, match="outside execution scope"):
        await load_turn_input_capsule(
            capsule_id=work_item.input_payload["capsule_id"],
            expected_digest=work_item.input_payload["capsule_digest"],
            principal_id=owner_id,
            workspace_id="different-workspace",
            thread_id=thread.id,
            work_item_id=receipt.work_item_id,
        )


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_turn_input_retention_preserves_active_work_and_purges_terminal(
    postgres_graph_context,
) -> None:
    from datetime import datetime, timezone

    from app.agentive.harness.turn_input import purge_expired_turn_input_capsules

    thread, owner_id, workspace_id = await _submission_context()
    receipt = await submit_chat_turn(
        _request(
            thread,
            owner_id,
            workspace_id,
            execution_context=ChatTurnExecutionContext(system_context="private"),
        )
    )
    work_item = await WorkItem.get(f"o.WorkItem.{receipt.work_item_id}")
    assert work_item is not None
    capsule_id = work_item.input_payload["capsule_id"]
    capsule = await HarnessTurnInputRecord.get(capsule_id)
    assert capsule is not None
    capsule.expires_at = "2000-01-01T00:00:00+00:00"
    await capsule.save()
    now = datetime(2026, 10, 4, tzinfo=timezone.utc)

    assert await purge_expired_turn_input_capsules(retention_days=90, now=now) == {
        "capsules_deleted": 0,
        "active_work_skipped": 1,
    }
    assert await HarnessTurnInputRecord.get(capsule_id) is not None

    work_item.status = "succeeded"
    await work_item.save()
    assert await purge_expired_turn_input_capsules(retention_days=90, now=now) == {
        "capsules_deleted": 1,
        "active_work_skipped": 0,
    }
    assert await HarnessTurnInputRecord.get(capsule_id) is None


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_turn_input_capsule_rewraps_under_thread_scope(
    postgres_graph_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.agentive.harness.key_rotation import rewrap_harness_session_records
    from app.agentive.harness.turn_input import (
        load_turn_input_capsule,
        persist_turn_input_capsule,
        thread_scope_key,
    )

    thread, owner_id, workspace_id = await _submission_context()
    keys = {"current": b"p" * 32, "previous": None}
    monkeypatch.setattr(
        "app.services.credential_crypto._resolve_key",
        lambda: (keys["current"], None),
    )
    monkeypatch.setattr(
        "app.services.credential_crypto._previous_key",
        lambda: keys["previous"],
    )
    execution_context = ChatTurnExecutionContext(system_context="rotation context")
    reference = await persist_turn_input_capsule(
        principal_id=owner_id,
        workspace_id=workspace_id,
        thread_id=thread.id,
        work_item_id="rotation-work-item",
        accepted_message_id="rotation-message",
        client_request_id="rotation-request",
        execution_context=execution_context,
        retention_days=90,
    )

    keys.update(current=b"q" * 32, previous=b"p" * 32)
    report = await rewrap_harness_session_records(
        scope_key=thread_scope_key(
            principal_id=owner_id,
            workspace_id=workspace_id,
            thread_id=thread.id,
        )
    )
    assert report == {
        "records_seen": 1,
        "records_rewrapped": 1,
        "HarnessTurnInputRecord": 1,
    }

    keys["previous"] = None
    loaded = await load_turn_input_capsule(
        capsule_id=reference["capsule_id"],
        expected_digest=reference["capsule_digest"],
        principal_id=owner_id,
        workspace_id=workspace_id,
        thread_id=thread.id,
        work_item_id="rotation-work-item",
    )
    assert loaded.execution_context == execution_context


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_principal_limit_is_shared_across_threads(
    postgres_graph_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "MAX_CONCURRENT_TURNS_PER_USER", 1)
    first_thread, owner_id, workspace_id = await _submission_context()
    await submit_chat_turn(_request(first_thread, owner_id, workspace_id))
    second_thread = await create_thread(
        user_id=owner_id,
        workspace_id=workspace_id,
        provider_id="integral_native",
    )

    with pytest.raises(ResourceConflictError) as conflict:
        await submit_chat_turn(
            _request(second_thread, owner_id, workspace_id, request_id="request-2")
        )

    assert conflict.value.details["reason"] == "user_turn_limit"
    assert await _queued_for_thread(second_thread.id) == ([], [])


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_concurrent_distinct_threads_obey_shared_principal_limit(
    postgres_graph_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "MAX_CONCURRENT_TURNS_PER_USER", 1)
    first_thread, owner_id, workspace_id = await _submission_context()
    second_thread = await create_thread(
        user_id=owner_id,
        workspace_id=workspace_id,
        provider_id="integral_native",
    )

    results = await asyncio.gather(
        submit_chat_turn(_request(first_thread, owner_id, workspace_id)),
        submit_chat_turn(
            _request(
                second_thread,
                owner_id,
                workspace_id,
                request_id="request-2",
            )
        ),
        return_exceptions=True,
    )

    receipts = [result for result in results if not isinstance(result, BaseException)]
    errors = [result for result in results if isinstance(result, BaseException)]
    assert len(receipts) == len(errors) == 1
    assert isinstance(errors[0], ResourceConflictError)
    assert errors[0].details["reason"] == "user_turn_limit"
    all_items = (await _queued_for_thread(first_thread.id))[0] + (
        await _queued_for_thread(second_thread.id)
    )[0]
    assert len(all_items) == 1
    assert len(await HarnessTurnAdmissionSlot.find({"principal_id": owner_id})) == 1


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_terminal_release_is_idempotent_and_cannot_clear_newer_turn(
    postgres_graph_context,
) -> None:
    from app.agentive.services.work_items import transition_work_item

    thread, owner_id, workspace_id = await _submission_context()
    first = await submit_chat_turn(_request(thread, owner_id, workspace_id))
    with pytest.raises(WorkError, match="terminal transition"):
        await release_chat_turn_admission(first.work_item_id)

    await transition_work_item(
        first.work_item_id, expected_status="queued", target="failed"
    )
    await release_chat_turn_admission(first.work_item_id)
    await release_chat_turn_admission(first.work_item_id)
    after_release = await ChatThread.get(thread.id)
    assert after_release is not None
    assert after_release.active_work_item_id == ""
    assert (
        await HarnessTurnAdmissionSlot.find({"work_item_id": first.work_item_id}) == []
    )

    second = await submit_chat_turn(
        _request(thread, owner_id, workspace_id, request_id="request-2")
    )
    await release_chat_turn_admission(first.work_item_id)
    after_stale_release = await ChatThread.get(thread.id)
    assert after_stale_release is not None
    assert after_stale_release.active_work_item_id == second.work_item_id
    assert (
        len(await HarnessTurnAdmissionSlot.find({"work_item_id": second.work_item_id}))
        == 1
    )


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_enqueue_then_failure_rolls_back_outbox_and_message(
    postgres_graph_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import chat_turn_submissions

    thread, owner_id, workspace_id = await _submission_context()
    original_enqueue = chat_turn_submissions.enqueue_work_item

    async def enqueue_then_fail(**kwargs: Any):
        await original_enqueue(**kwargs)
        raise RuntimeError("injected post-enqueue failure")

    monkeypatch.setattr(chat_turn_submissions, "enqueue_work_item", enqueue_then_fail)
    with pytest.raises(RuntimeError, match="injected post-enqueue failure"):
        await submit_chat_turn(_request(thread, owner_id, workspace_id))

    items, outbox = await _queued_for_thread(thread.id)
    messages = await thread.nodes(
        edge=[CONTAINS], node=["ChatMessage"], direction="out", limit=10
    )
    assert items == []
    assert outbox == []
    assert [message for message in messages if message.role == "user"] == []


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_message_edge_failure_rolls_back_all_submission_records(
    postgres_graph_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    thread, owner_id, workspace_id = await _submission_context()

    async def fail_connect(self, *_args: Any, **_kwargs: Any):
        raise RuntimeError("injected message edge failure")

    monkeypatch.setattr(ChatThread, "connect", fail_connect)
    with pytest.raises(RuntimeError, match="injected message edge failure"):
        await submit_chat_turn(_request(thread, owner_id, workspace_id))

    items, outbox = await _queued_for_thread(thread.id)
    assert items == []
    assert outbox == []
    assert await ChatMessage.find({"thread_id": thread.id}) == []
    persisted_thread = await ChatThread.get(thread.id)
    assert persisted_thread is not None
    assert persisted_thread.active_work_item_id == ""
    assert await HarnessTurnAdmissionSlot.find({"principal_id": owner_id}) == []


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_capsule_persist_failure_rolls_back_submission_transaction(
    postgres_graph_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.agentive.harness import turn_input

    thread, owner_id, workspace_id = await _submission_context()

    async def fail_capsule(**_kwargs: Any):
        raise RuntimeError("injected capsule encryption failure")

    monkeypatch.setattr(turn_input, "persist_turn_input_capsule", fail_capsule)
    with pytest.raises(RuntimeError, match="injected capsule encryption failure"):
        await submit_chat_turn(
            _request(
                thread,
                owner_id,
                workspace_id,
                execution_context=ChatTurnExecutionContext(system_context="private"),
            )
        )

    items, outbox = await _queued_for_thread(thread.id)
    messages = await thread.nodes(
        edge=[CONTAINS], node=["ChatMessage"], direction="out", limit=10
    )
    assert items == []
    assert outbox == []
    assert [message for message in messages if message.role == "user"] == []
    assert await HarnessTurnInputRecord.find({"thread_id": thread.id}) == []
    assert await HarnessTurnAdmissionSlot.find({"principal_id": owner_id}) == []


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_assistant_result_is_fenced_stable_and_idempotent(
    postgres_graph_context,
) -> None:
    """A WorkItem retry returns one graph-linked assistant result."""
    thread, owner_id, workspace_id = await _submission_context()
    accepted = await submit_chat_turn(_request(thread, owner_id, workspace_id))
    claimed = await work_items.claim_due_candidate(
        worker_id=f"transcript-worker-{uuid.uuid4().hex}",
        lease_seconds=60,
        work_item_id=accepted.work_item_id,
    )
    assert claimed is not None
    context = build_work_execution_context(
        work_item=claimed,
        logical_step_key="native-chat-transcript:0",
    )
    parts = [{"type": "text", "text": "This workspace has no tracks."}]
    metadata = {
        "steps": [
            {
                "modelId": "openai/gpt-test",
                "provider": "openai",
                "usage": {"inputTokens": 12, "outputTokens": 7},
            }
        ],
        "timing": {"totalMs": 342},
    }

    first = await persist_work_item_assistant_result(
        context=context,
        parts=parts,
        provider_metadata=metadata,
    )
    retry = await persist_work_item_assistant_result(
        context=context,
        parts=parts,
        provider_metadata=metadata,
    )

    assert retry.id == first.id
    assert retry.parent_id == accepted.message_id
    assert retry.parts == parts
    assert retry.provider_metadata == {
        "streaming": True,
        "steps": metadata["steps"],
        "timing": metadata["timing"],
    }
    assistant_messages = [
        message
        for message in await thread.nodes(
            edge=[CONTAINS], node=["ChatMessage"], direction="out", limit=20
        )
        if message.role == "assistant"
    ]
    assert [message.id for message in assistant_messages] == [first.id]

    with pytest.raises(WorkError) as conflict:
        await persist_work_item_assistant_result(
            context=context,
            parts=[{"type": "text", "text": "Different answer"}],
            provider_metadata=metadata,
        )
    assert conflict.value.code == "work.idempotency_conflict"


@pytest.mark.asyncio
async def test_assistant_result_rejects_reasoning_and_unapproved_metadata() -> None:
    """Transcript projection refuses private parts and raw provider payloads."""
    from app.services.chat_turn_transcript import _public_metadata, _public_parts

    with pytest.raises(ValueError, match="unsupported part"):
        _public_parts([{"type": "reasoning", "text": "private chain of thought"}])
    with pytest.raises(ValueError, match="unsupported fields"):
        _public_metadata({"finalPayload": {"system_prompt": "private"}})
