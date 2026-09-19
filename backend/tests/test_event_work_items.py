"""ChangeEvent trigger consumer enqueues durable work (Task 9)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.agentive.services import work_events
from app.agentive.work_models import EventTriggerDeclaration, WorkItem
from app.utils.time import utc_now_iso


@pytest.mark.asyncio
async def test_event_idempotency_key() -> None:
    assert (
        work_events.event_work_idempotency_key(event_id="e1", trigger_key="t1")
        == "event:e1:t1"
    )


@pytest.mark.asyncio
async def test_enqueue_event_trigger_idempotent() -> None:
    first = await work_events.enqueue_event_trigger(
        event_id="ev-1",
        trigger_key="on_entry_create",
        principal_id="u1",
        workspace_id="ws1",
        capability_key="integral_list_entries",
        action="entry.create",
        resource_type="entry",
        resource_id="e1",
    )
    second = await work_events.enqueue_event_trigger(
        event_id="ev-1",
        trigger_key="on_entry_create",
        principal_id="u1",
        workspace_id="ws1",
        capability_key="integral_list_entries",
        action="entry.create",
        resource_type="entry",
        resource_id="e1",
    )
    assert first.work_item_id == second.work_item_id
    assert first.kind == "event_trigger"
    rows = await WorkItem.find(
        {"context.idempotency_key": "event:ev-1:on_entry_create"}
    )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_declaration_match_and_checkpoint_advance() -> None:
    decl, _ = await EventTriggerDeclaration.create_if_absent(
        id="o.EventTriggerDeclaration.test-entry-create",
        trigger_key="test-entry-create",
        action="entry.create",
        resource_type="entry",
        scope_prefix="",
        capability_key="integral_list_entries",
        turn_template="",
        enabled=True,
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
    )
    assert work_events.declaration_matches(
        decl, action="entry.create", resource_type="entry", scope="ws:abc"
    )
    assert not work_events.declaration_matches(
        decl, action="entry.update", resource_type="entry", scope="ws:abc"
    )

    cp = await work_events.get_or_create_checkpoint("test-consumer")
    assert cp.last_event_id == ""
    await work_events.advance_checkpoint(
        cp, logged_at=datetime.now(timezone.utc).isoformat(), event_id="evt-x"
    )
    reloaded = await work_events.get_or_create_checkpoint("test-consumer")
    assert reloaded.last_event_id == "evt-x"
