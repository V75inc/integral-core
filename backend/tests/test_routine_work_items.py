"""Routine fires enqueue durable WorkItems (Task 8)."""

from __future__ import annotations

import pytest

from app.agentive.services import routine_tasks, work_items
from app.agentive.work_models import WorkItem


@pytest.mark.asyncio
async def test_enqueue_routine_turn_idempotent() -> None:
    from tests.test_routine_tasks import _bootstrap_user_workspace_track_thread

    user_id, workspace_id, _, thread = await _bootstrap_user_workspace_track_thread(
        "routine-key@example.com"
    )
    routine = await routine_tasks.create_routine_task(
        user_id=user_id,
        workspace_id=workspace_id,
        thread_id=thread.id,
        instruction="Synthetic queue proof.",
        cron="0 9 * * *",
    )
    first = await routine_tasks.enqueue_routine_turn_work(
        routine_id=routine.id,
        principal_id=user_id,
        workspace_id=workspace_id,
        scheduled_for="2026-09-19T12:00:00+00:00",
        thread_id=thread.id,
    )
    second = await routine_tasks.enqueue_routine_turn_work(
        routine_id=routine.id,
        principal_id=user_id,
        workspace_id=workspace_id,
        scheduled_for="2026-09-19T12:00:00+00:00",
        thread_id=thread.id,
    )
    assert first.work_item_id == second.work_item_id
    assert first.kind == "routine_turn"
    assert first.idempotency_key == f"routine:{routine.id}:2026-09-19T12:00:00+00:00"
    rows = await WorkItem.find({"context.idempotency_key": first.idempotency_key})
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_idempotency_key_helper() -> None:
    key = routine_tasks.routine_work_idempotency_key(
        routine_id="abc", scheduled_for="t0"
    )
    assert key == "routine:abc:t0"
