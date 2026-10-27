"""Bounded monitoring executes fresh scoped reads instead of delivering task text."""

import pytest

from app.agentive.tooling.dispatch import dispatch_tool
from tests.test_routine_tasks import _bootstrap_user_workspace_track_thread

pytestmark = pytest.mark.smoke


@pytest.mark.parametrize(
    "prompt",
    [
        "For the next 5 minutes, for every minute tell me how many entries are in this workspace",
        "Every hour send me an updated entry count",
        "Check this workspace every day and report how many entries there are",
    ],
)
def test_live_count_schedules_are_discoverable_as_scheduling(prompt):
    """Periodic query requests must discover the workflow that owns cadence."""
    from app.agentive.harness.capability_search import (
        _rank,
        _searchable_skill_description,
    )
    from app.agentive.services.agent_skills import list_core_skills

    ranked = _rank(
        prompt,
        [
            (row["key"], _searchable_skill_description(row["description"]))
            for row in list_core_skills()
        ],
    )
    assert ranked[0][0] == "integral-scheduling", ranked[:3]


@pytest.mark.asyncio
async def test_five_agent_updates_read_changed_counts_and_then_stop(monkeypatch):
    """Approved agent routines re-read state and respect their delivery budget."""
    from app.agentive.nodes import RoutineTask
    from app.agentive.services.staging_apply import bless_and_execute
    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api.entries import create_entry
    from app.services import routine_task_scheduler as scheduler

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "66" * 32)
    user_id, workspace_id, track_id, thread = (
        await _bootstrap_user_workspace_track_thread("count-routine@example.com")
    )
    proposal = await dispatch_tool(
        "integral_schedule_task",
        {
            "instruction": "Read and report the current accessible workspace entry count.",
            "delivery_mode": "agent",
            "cron": "* * * * *",
            "max_runs": 5,
        },
        principal_id=user_id,
        scope=workspace_id,
        session_id=thread.id,
    )
    assert not proposal.is_error, proposal
    assert not await RoutineTask.find({"user_id": user_id})
    approved = await bless_and_execute(user_id=user_id, token=proposal.data["token"])
    assert not approved["execute_result"].get("error"), approved
    routines = await RoutineTask.find({"user_id": user_id})
    assert len(routines) == 1
    routine = routines[0]
    assert routine.delivery_mode == "agent"
    assert routine.cron == "* * * * *"
    assert routine.max_runs == 5
    assert routine.thread_id == thread.id

    # Replace only cloud inference: exercise real scheduling, broker admission,
    # live count reads and completion against graph records changed between runs.
    counts = []

    async def read_count(task):
        from app.agentive.services.work_execution_binding import current_work_execution
        from app.services import chat_turn_registry

        execution = current_work_execution()
        handle = await chat_turn_registry.acquire_turn(
            thread_id=task.thread_id, user_id=task.user_id, origin="routine_task"
        )
        handle.work_item_id = execution.work_item_id
        handle.work_lease_fence = execution.lease_fence
        result = await dispatch_tool(
            "integral_count_entries",
            {"group_by": "track"},
            principal_id=task.user_id,
            scope=task.workspace_id,
            session_id=task.thread_id,
        )
        assert not result.is_error, result
        counts.append(result.data["total_matched"])
        return True, None

    async def forbidden_literal_delivery(*args: object):
        raise AssertionError("A live count must not use saved-text delivery")

    monkeypatch.setattr(scheduler, "_run_agent_turn", read_count)
    monkeypatch.setattr(
        "app.agentive.services.routine_tasks._deliver_reminder",
        forbidden_literal_delivery,
    )
    for index in range(5):
        entry = await invoke_route_in_process(
            create_entry,
            principal_id=user_id,
            scope=workspace_id,
            track_id=track_id,
            title=f"Monitoring record {index + 1}",
        )
        assert "entry" in entry, entry
        await scheduler._run_one(routine.id)
        from app.services import chat_turn_registry

        assert chat_turn_registry.get_turn(thread.id) is None
        current = await RoutineTask.get(routine.id)
        assert current.run_count == index + 1
    assert counts == [1, 2, 3, 4, 5]
    assert current.status == "completed"
    await scheduler._run_one(routine.id)
    assert counts == [1, 2, 3, 4, 5]


def test_minute_cadence_advances_across_the_hour_boundary():
    """Every-minute cron must not wait for a minute range in the next hour."""
    from app.agentive.services.routine_tasks import compute_next_run_at

    assert (
        compute_next_run_at(
            "* * * * *", "America/Guyana", base_iso="2026-10-26T20:59:30+00:00"
        )
        == "2026-10-26T21:00:00+00:00"
    )


@pytest.mark.asyncio
async def test_old_routine_cleanup_cannot_release_a_new_attempt():
    """Cleanup is fenced by the WorkItem attempt and exact turn identity."""
    from types import SimpleNamespace

    from app.agentive.services.work_worker import _release_routine_chat_turn
    from app.services import chat_turn_registry

    await chat_turn_registry.reset_registry_for_tests()
    handle = await chat_turn_registry.acquire_turn(
        thread_id="routine-fence-test", user_id="test", origin="routine_task"
    )
    handle.work_item_id = "same-work"
    handle.work_lease_fence = 2
    try:
        await _release_routine_chat_turn(
            SimpleNamespace(
                thread_id=handle.thread_id, work_item_id="same-work", lease_fence=1
            )
        )
        assert chat_turn_registry.get_turn(handle.thread_id) is handle
        assert not await chat_turn_registry.release_turn(
            handle.thread_id, expected_turn_id="old-turn"
        )
        assert chat_turn_registry.get_turn(handle.thread_id) is handle
    finally:
        await chat_turn_registry.release_turn(handle.thread_id)
