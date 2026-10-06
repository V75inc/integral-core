"""PostgreSQL checks for fenced, sequenced, tenant-scoped chat events."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from jvspatial.core.context import GraphContext, set_default_context

from app.agentive.services import work_items
from app.agentive.services.work_execution import build_work_execution_context
from app.agentive.work_models import WorkItem
from app.models.harness_records import HarnessChatEventRecord
from app.schemas.agentive.chat_events import ChatEventReplayPage
from app.schemas.agentive.work import WorkExecutionContext
from app.services.chat_turn_events import (
    append_work_item_chat_event,
    replay_work_item_chat_events,
)


@pytest.fixture
def postgres_graph_context(postgres_raw_db: Any) -> Any:
    """Bind event and WorkItem writes to the isolated PostgreSQL database."""
    from jvspatial.core.context import _default_context_var

    token = set_default_context(GraphContext(database=postgres_raw_db))
    try:
        yield
    finally:
        _default_context_var.reset(token)


async def _claimed_chat_turn() -> tuple[WorkItem, WorkExecutionContext]:
    suffix = uuid.uuid4().hex
    queued = await work_items.enqueue_work_item(
        kind="chat_turn",
        origin="chat-event-contract",
        principal_id=f"event-user-{suffix}",
        workspace_id=f"event-workspace-{suffix}",
        thread_id=f"event-thread-{suffix}",
        idempotency_key=f"event-request-{suffix}",
        input_payload={"accepted_message_id": f"message-{suffix}"},
    )
    claimed = await work_items.claim_due_candidate(
        worker_id=f"event-worker-{suffix}",
        lease_seconds=60,
        work_item_id=queued.work_item_id,
    )
    assert claimed is not None
    context = build_work_execution_context(
        work_item=claimed,
        logical_step_key="native-chat-events:0",
    )
    return claimed, context


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_postgres_event_append_is_fenced_encrypted_and_idempotent(
    postgres_graph_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Duplicate event keys reuse their committed sequence and content."""
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "A" * 43 + "=")
    item, context = await _claimed_chat_turn()
    event = {"type": "text-delta", "delta": "first fragment"}

    first = await append_work_item_chat_event(
        context=context, event_key="provider-event-1", event=event
    )
    retry = await append_work_item_chat_event(
        context=context,
        event_key="provider-event-1",
        event={**event, "unapproved": "not persisted"},
    )

    assert first == retry == {"sequence": 1, **event}
    record_filter = {"work_item_id": item.work_item_id}
    records = await HarnessChatEventRecord.find(record_filter)
    assert len(records) == 1
    assert records[0].payload_ciphertext.startswith("v1:")
    assert "first fragment" not in records[0].payload_ciphertext


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_postgres_event_replay_reports_cursor_gaps_and_exact_scope(
    postgres_graph_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replay reports missing committed sequences without crossing tenants."""
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "A" * 43 + "=")
    item, context = await _claimed_chat_turn()
    await append_work_item_chat_event(
        context=context,
        event_key="provider-event-1",
        event={"type": "text-delta", "delta": "one"},
    )
    await append_work_item_chat_event(
        context=context,
        event_key="provider-event-2",
        event={"type": "message-boundary"},
    )

    page = await replay_work_item_chat_events(
        principal_id=context.principal_id,
        workspace_id=context.workspace_id,
        thread_id=context.thread_id,
        work_item_id=item.work_item_id,
    )
    assert isinstance(page, ChatEventReplayPage)
    assert [event["sequence"] for event in page.events] == [1, 2]
    assert page.committed_through == 2
    assert page.next_after_sequence == 2
    assert page.work_status == "running"
    assert page.gap is False

    first_page = await replay_work_item_chat_events(
        principal_id=context.principal_id,
        workspace_id=context.workspace_id,
        thread_id=context.thread_id,
        work_item_id=item.work_item_id,
        limit=1,
    )
    assert [event["sequence"] for event in first_page.events] == [1]
    assert first_page.next_after_sequence == 1
    assert first_page.has_more is True
    second_page = await replay_work_item_chat_events(
        principal_id=context.principal_id,
        workspace_id=context.workspace_id,
        thread_id=context.thread_id,
        work_item_id=item.work_item_id,
        after_sequence=first_page.next_after_sequence,
        limit=1,
    )
    assert [event["sequence"] for event in second_page.events] == [2]
    assert second_page.next_after_sequence == 2
    assert second_page.has_more is False

    first = next(
        record
        for record in await HarnessChatEventRecord.find(
            {"work_item_id": item.work_item_id}
        )
        if record.sequence == 1
    )
    await first.delete()
    page_with_gap = await replay_work_item_chat_events(
        principal_id=context.principal_id,
        workspace_id=context.workspace_id,
        thread_id=context.thread_id,
        work_item_id=item.work_item_id,
    )
    assert page_with_gap.gap is True
    assert [event["sequence"] for event in page_with_gap.events] == [2]

    with pytest.raises(ValueError, match="scope mismatch"):
        await replay_work_item_chat_events(
            principal_id=context.principal_id,
            workspace_id="other-workspace",
            thread_id=context.thread_id,
            work_item_id=item.work_item_id,
        )


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_postgres_concurrent_chat_event_append_has_one_sequence_order(
    postgres_graph_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Independent transactions serialize sequence allocation on the turn."""
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "A" * 43 + "=")
    _item, context = await _claimed_chat_turn()

    events = await asyncio.gather(
        append_work_item_chat_event(
            context=context,
            event_key="provider-event-a",
            event={"type": "text-delta", "delta": "alpha"},
        ),
        append_work_item_chat_event(
            context=context,
            event_key="provider-event-b",
            event={"type": "text-delta", "delta": "beta"},
        ),
    )

    assert {event["sequence"] for event in events} == {1, 2}
