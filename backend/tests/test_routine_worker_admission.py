"""A recovered routine fire must repeat admission before its agent boundary."""

import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.smoke]


@pytest.mark.parametrize(
    "change",
    [
        "paused",
        "cancelled",
        "completed",
        "thread_archived",
        "source_app_uninstalled",
        "principal_mismatch",
        "workspace_mismatch",
        "thread_mismatch",
        "permission_refused",
        "write_target_deleted",
        "lease_cancelled",
        "lease_expired",
    ],
)
async def test_recovered_routine_refuses_changed_admission(change, monkeypatch):
    from app.agentive.services import routine_tasks, work_items, work_worker
    from app.agentive.work_models import WorkItem
    from app.models.edges import CONTAINS, OWNS
    from app.models.nodes import App, Entry, Track, Workspace
    from app.schemas.agentive.work import WorkError
    from app.services import routine_task_scheduler as scheduler
    from app.services.permissions import get_user_node
    from tests.test_routine_tasks import _bootstrap_user_workspace_track_thread

    user_id, workspace_id, track_id, thread = (
        await _bootstrap_user_workspace_track_thread(
            f"routine-worker-{change}@example.com"
        )
    )
    app = await App.create(
        name="Routine source",
        workspace_id=workspace_id,
        owner_user_id=user_id,
        lifecycle_state="active",
    )
    await (await Workspace.get(workspace_id)).connect(app, edge=CONTAINS)
    await (await get_user_node(user_id)).connect(app, edge=OWNS)
    allowed_entry = await Entry.create(
        title="Allowed routine record", track_id=track_id, author_id=user_id
    )
    await (await Track.get(track_id)).connect(allowed_entry, edge=CONTAINS)
    routine = await routine_tasks.create_routine_task(
        user_id=user_id,
        workspace_id=workspace_id,
        thread_id=thread.id,
        instruction="Read saved status only.",
        cron="0 9 * * *",
        source_app_id=app.id,
        write_scope=[{"resource_type": "entry", "resource_id": allowed_entry.id}],
    )
    item = await routine_tasks.enqueue_routine_turn_work(
        routine_id=routine.id,
        principal_id="different-owner" if change == "principal_mismatch" else user_id,
        workspace_id=(
            "different-workspace" if change == "workspace_mismatch" else workspace_id
        ),
        thread_id="different-thread" if change == "thread_mismatch" else thread.id,
        scheduled_for="2026-10-11T09:00:00+00:00",
    )
    if change in {"paused", "cancelled", "completed"}:
        routine.status = change
        await routine.save()
    elif change == "thread_archived":
        thread.archived = True
        await thread.save()
    elif change == "source_app_uninstalled":
        app.lifecycle_state = "uninstalled"
        await app.save()
    elif change == "permission_refused":

        async def refuse(_task):
            return "workspace access revoked after queueing"

        monkeypatch.setattr(scheduler, "_permission_gate", refuse)
    elif change == "write_target_deleted":
        await allowed_entry.delete()
    elif change in {"lease_cancelled", "lease_expired"}:
        original_gate = scheduler._permission_gate

        async def change_fence(task):
            reason = await original_gate(task)
            assert reason is None
            if change == "lease_cancelled":
                await work_items.cancel_work_item(item.work_item_id)
            else:
                leased = await WorkItem.get(item.id)
                leased.lease_expires_at = "2020-01-01T00:00:00+00:00"
                await leased.save()
            return None

        monkeypatch.setattr(scheduler, "_permission_gate", change_fence)
    calls = []

    async def observe_turn(task):
        calls.append(task.id)
        return True, None

    monkeypatch.setattr(scheduler, "_run_agent_turn", observe_turn)
    before = await routine.export(flat=True)

    async def execute():
        return await work_worker.process_one_due_item(
            worker_id="routine-recovery-probe",
            work_item_id=item.work_item_id,
            lease_seconds=30,
        )

    if change in {"lease_cancelled", "lease_expired"}:
        with pytest.raises(WorkError) as refused:
            await execute()
        assert refused.value.code == (
            "work.cancelled" if change == "lease_cancelled" else "work.lease_lost"
        )
        done = await WorkItem.get(item.id)
        assert done.status != "succeeded"
    else:
        done = await execute()
        assert done is not None and done.status == "failed"
        assert done.failure["code"] == "work.policy_denied"
    assert calls == [], "a refused recovered fire reached the agent boundary"
    assert await (await type(routine).get(routine.id)).export(flat=True) == before


async def test_active_recovered_routine_runs_once_under_matching_identity(monkeypatch):
    from app.agentive.services import routine_tasks, work_worker
    from app.services import routine_task_scheduler as scheduler
    from tests.test_routine_tasks import _bootstrap_user_workspace_track_thread

    user_id, workspace_id, _, thread = await _bootstrap_user_workspace_track_thread(
        "routine-worker-admitted@example.com"
    )
    routine = await routine_tasks.create_routine_task(
        user_id=user_id,
        workspace_id=workspace_id,
        thread_id=thread.id,
        instruction="Read saved status only.",
        cron="0 9 * * *",
    )
    item = await routine_tasks.enqueue_routine_turn_work(
        routine_id=routine.id,
        principal_id=user_id,
        workspace_id=workspace_id,
        thread_id=thread.id,
        scheduled_for="2026-10-11T09:00:00+00:00",
    )
    calls = []

    async def observe_turn(task):
        calls.append((task.id, task.user_id, task.workspace_id, task.thread_id))
        return True, None

    monkeypatch.setattr(scheduler, "_run_agent_turn", observe_turn)
    done = await work_worker.process_one_due_item(
        worker_id="routine-recovery-probe",
        work_item_id=item.work_item_id,
        lease_seconds=30,
    )
    assert done is not None and done.status == "succeeded"
    replay = await work_worker.process_one_due_item(
        worker_id="routine-recovery-probe",
        work_item_id=item.work_item_id,
        lease_seconds=30,
    )
    assert replay is None
    assert calls == [(routine.id, user_id, workspace_id, thread.id)]
