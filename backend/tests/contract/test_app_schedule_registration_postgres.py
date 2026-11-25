"""App schedule registration defers chat and preserves existing mandates."""

import asyncio

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.agentive.nodes import RoutineTask
from app.agentive.services.routine_tasks import enqueue_routine_turn_work
from app.agentive.services.routine_work_admission import assert_routine_work_current
from app.agentive.services.skill_registry import register_skill
from app.agentive.services.uplink_registry import (
    _schedule_instruction,
    register_app_agent,
)
from app.agentive.workspace_agent_profile import _app_slug
from app.schemas.agentive.work import WorkError
from app.services.app_service import create_app_for_user
from app.services.chat_threads import create_thread, list_threads
from app.services.routine_task_scheduler import _prepare_scheduled_run
from tests.contract.test_staging_command_apply_postgres import proposal

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


async def test_generated_schedule_repair_fences_queued_legacy_work(
    postgres_raw_db, monkeypatch
):
    monkeypatch.setattr(
        "app.agentive.services.uplink_registry._scheduler_available", lambda: True
    )
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, _, _ = await proposal()
        app = await create_app_for_user(
            auth.id, "Schedule selection", workspace_id=workspace.id
        )
        for key in ("daily_read", "manual_write"):
            await register_skill(
                app_id=app.id,
                workspace_id=workspace.id,
                skill_spec={
                    "key": key,
                    "kind": "declarative",
                    "prompt_template_ref": f"skills/{key}/SKILL.md",
                },
            )
        spec = {
            "key": "resident",
            "skills": ["daily_read", "manual_write"],
            "default_schedules": [
                {
                    "name": "daily",
                    "cron": "0 9 * * *",
                    "skill": "daily_read",
                    "params": {"days": 7},
                }
            ],
        }
        config = await register_app_agent(app.id, workspace.id, spec)
        routine = await RoutineTask.get(config.scheduled_runs[0]["routine_task_id"])
        correct = routine.instruction
        # Simulate the stored pre-upgrade binding and generated instruction.
        legacy_thread = await create_thread(
            user_id=auth.id,
            workspace_id=workspace.id,
            provider_id="integral_native",
            agent_id=routine.agent_id,
            title=routine.pending_thread_title,
        )
        routine.thread_id = legacy_thread.id
        routine.pending_thread_title = ""
        routine.instruction = _schedule_instruction(
            slug=_app_slug(app),
            agent_key="resident",
            skills=spec["skills"],
            description="",
        )
        await routine.save()
        queued = await enqueue_routine_turn_work(
            routine_id=routine.id,
            principal_id=auth.id,
            workspace_id=workspace.id,
            thread_id=routine.thread_id,
            scheduled_for="legacy-fire",
        )
        await assert_routine_work_current(queued)
        refreshed = await register_app_agent(app.id, workspace.id, spec)
        assert refreshed.scheduled_runs[0]["routine_task_id"] == routine.id
        saved = await RoutineTask.get(routine.id)
        assert saved.instruction == correct
        assert saved.write_scope == routine.write_scope
        assert saved.thread_id == legacy_thread.id
        assert saved.next_run_at == routine.next_run_at
        with pytest.raises(WorkError, match="mandate"):
            await assert_routine_work_current(queued)
        fresh = await enqueue_routine_turn_work(
            routine_id=routine.id,
            principal_id=auth.id,
            workspace_id=workspace.id,
            thread_id=routine.thread_id,
            scheduled_for="corrected-fire",
        )
        await assert_routine_work_current(fresh)


async def test_app_registration_defers_chat_until_first_eligible_dispatch(
    postgres_raw_db, monkeypatch
):
    monkeypatch.setattr(
        "app.agentive.services.uplink_registry._scheduler_available", lambda: True
    )
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, _, _ = await proposal()
        app = await create_app_for_user(
            auth.id, "Workshop schedule fixture", workspace_id=workspace.id
        )
        spec = {
            "key": "resident",
            "default_schedules": [{"cron": "0 9 * * *", "description": "Daily view"}],
        }

        async def history():
            return await list_threads(user_id=auth.id, workspace_id=workspace.id)

        assert await history() == []
        config = await register_app_agent(app.id, workspace.id, spec)
        schedule_id = config.scheduled_runs[0]["routine_task_id"]
        stored = await RoutineTask.get(schedule_id)
        assert stored is not None and stored.awaits_app_schedule_thread
        assert stored.status == "active" and stored.next_run_at
        assert await history() == []
        reloaded = await register_app_agent(app.id, workspace.id, spec)
        assert reloaded.scheduled_runs[0]["routine_task_id"] == schedule_id
        assert await history() == []
        stored.status = "paused"
        await stored.save()
        assert await _prepare_scheduled_run(stored) == (None, None)
        assert await history() == []
        stored.status = "active"
        await stored.save()
        with pytest.raises(WorkError, match="binding"):
            await enqueue_routine_turn_work(
                routine_id=schedule_id,
                principal_id=auth.id,
                workspace_id=workspace.id,
                thread_id="",
                scheduled_for="unbound",
            )

        explicit = await create_thread(
            user_id=auth.id,
            workspace_id=workspace.id,
            provider_id="integral_native",
            title="Intentional chat",
        )
        assert [thread.id for thread in await history()] == [explicit.id]

        # Even two stale scheduler snapshots bind only one thread in Postgres.
        one, two = await asyncio.gather(
            _prepare_scheduled_run(stored), _prepare_scheduled_run(stored)
        )
        first, first_reason = one
        second, second_reason = two
        assert first_reason is second_reason is None
        assert first is not None and second is not None
        assert first.thread_id == second.thread_id
        assert first.thread_id and first.thread_id != explicit.id
        assert first.pending_thread_title == ""
        assert first.agent_id == stored.agent_id
        assert {thread.id for thread in await history()} == {
            explicit.id,
            first.thread_id,
        }
        queued = await enqueue_routine_turn_work(
            routine_id=schedule_id,
            principal_id=auth.id,
            workspace_id=workspace.id,
            thread_id=first.thread_id,
            scheduled_for="first-bound-fire",
        )
        await assert_routine_work_current(queued)
        later, later_reason = await _prepare_scheduled_run(first)
        assert later_reason is None
        assert later is not None and later.thread_id == first.thread_id
