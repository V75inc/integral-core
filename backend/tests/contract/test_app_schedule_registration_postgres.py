"""Generated schedule repair preserves identity and fences older queued mandates."""

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
