"""Scheduler bookkeeping must not replace later owner edits or revive a stop."""

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.agentive.services import routine_tasks
from app.services import routine_task_scheduler as scheduler
from tests.contract.test_staging_routine_commands_postgres import routine_proposal

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


@pytest.mark.parametrize("progress", ["failure", "skip", "auto_pause"])
@pytest.mark.parametrize("later", ["edit", "cancel"])
async def test_stale_progress_preserves_later_authorized_state(
    postgres_raw_db, monkeypatch, progress, later
):
    async def quiet(*args, **kwargs):
        pass

    monkeypatch.setattr(scheduler, "_emit_run_event", quiet)
    monkeypatch.setattr(scheduler, "_notify_auto_paused", quiet)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, routine, _ = await routine_proposal("routine_task_update")
        stale = routine.model_copy()
        if later == "edit":
            await routine_tasks.update_routine_task(
                user_id=auth.id,
                routine_id=routine.id,
                instruction="Later reviewed instruction",
            )
        else:
            await routine_tasks.cancel_routine_task(
                user_id=auth.id, routine_id=routine.id
            )
        before = await postgres_raw_db.get("node", routine.id)
        if progress == "failure":
            await scheduler._record_failed_run(stale, reason="Synthetic failed run")
        elif progress == "skip":
            await scheduler._record_skipped_run(stale, reason="Synthetic busy turn")
        else:
            await scheduler._auto_pause(stale, reason="Synthetic admission refusal")
        after = await postgres_raw_db.get("node", routine.id)
        if later == "edit":
            assert after["context"]["instruction"] == "Later reviewed instruction"
        else:
            assert after == before
