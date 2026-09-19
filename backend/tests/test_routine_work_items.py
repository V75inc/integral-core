"""Routine fires enqueue durable WorkItems (Task 8)."""

from __future__ import annotations

import pytest

from app.agentive.services import routine_tasks, work_items
from app.agentive.work_models import WorkItem


@pytest.mark.asyncio
async def test_enqueue_routine_turn_idempotent() -> None:
    first = await routine_tasks.enqueue_routine_turn_work(
        routine_id="rt-1",
        principal_id="u1",
        workspace_id="ws1",
        scheduled_for="2026-09-19T12:00:00+00:00",
        thread_id="th1",
    )
    second = await routine_tasks.enqueue_routine_turn_work(
        routine_id="rt-1",
        principal_id="u1",
        workspace_id="ws1",
        scheduled_for="2026-09-19T12:00:00+00:00",
        thread_id="th1",
    )
    assert first.work_item_id == second.work_item_id
    assert first.kind == "routine_turn"
    assert first.idempotency_key == "routine:rt-1:2026-09-19T12:00:00+00:00"
    rows = await WorkItem.find(
        {"context.idempotency_key": first.idempotency_key}
    )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_idempotency_key_helper() -> None:
    key = routine_tasks.routine_work_idempotency_key(
        routine_id="abc", scheduled_for="t0"
    )
    assert key == "routine:abc:t0"
