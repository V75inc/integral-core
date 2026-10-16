"""Routine command recovery is separate from scheduled fire idempotency."""

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.agentive.nodes import RoutineTask
from app.agentive.services import routine_tasks, staging_apply, staging_execution
from app.agentive.staging import StagingError, create_staged_change
from app.services.agent_scope import current_scope_workspace_id
from app.services.chat_threads import create_thread
from tests.contract.test_staging_command_apply_postgres import proposal

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]

ALL_KINDS = (
    "routine_task_create",
    "routine_task_update",
    "routine_task_cancel",
    "routine_task_purge",
)

KINDS = (
    "routine_task_create",
    "routine_task_update",
)


async def routine_proposal(kind):
    auth, workspace, _, _ = await proposal()
    thread = await create_thread(
        user_id=auth.id, provider_id="integral_native", workspace_id=workspace.id
    )
    payload = {
        "workspace_id": workspace.id,
        "thread_id": thread.id,
        "instruction": "Synthetic routine recovery proof",
        "delivery_mode": "reminder",
        "cron": "0 * * * *",
        "timezone": "UTC",
    }
    routine = None
    if kind != "routine_task_create":
        routine = await routine_tasks.create_routine_task(user_id=auth.id, **payload)
        payload = {"routine_id": routine.id}
        if kind == "routine_task_update":
            payload["status"] = "paused"
    reset = current_scope_workspace_id.set(workspace.id)
    try:
        change = await create_staged_change(
            user_id=auth.id,
            session_id=None,
            kind=kind,
            summary="Change one routine",
            diff_human="One exact schedule command",
            diff_machine={},
            payload=payload,
        )
    finally:
        current_scope_workspace_id.reset(reset)
    return auth, workspace, routine, change


@pytest.mark.parametrize("kind", ALL_KINDS)
async def test_routine_completion_loss_has_exact_applied_receipt(
    postgres_raw_db, monkeypatch, kind
):
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "aa" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, routine, change = await routine_proposal(kind)

        async def unavailable(**kwargs):
            raise StagingError(
                "completion_unavailable", "Injected lost routine completion"
            )

        monkeypatch.setattr(staging_apply, "consume_token", unavailable)
        result = await staging_apply.bless_and_execute(
            user_id=auth.id, token=change.token
        )
        assert result["application_state"] == "reconciliation_required", result
        if kind == "routine_task_create":
            rows = await RoutineTask.find({"user_id": auth.id})
            assert len(rows) == 1
        else:
            row = await postgres_raw_db.get("node", routine.id)
            if kind == "routine_task_purge":
                assert row is None
            else:
                assert row["context"]["status"] == (
                    "paused" if kind == "routine_task_update" else "cancelled"
                )
        record = await postgres_raw_db.get(
            "object", staging_execution.execution_record_id(change.token)
        )
        assert record is not None and record["context"]["state"] == "applied"


@pytest.mark.parametrize("kind", ALL_KINDS)
async def test_routine_receipt_storage_failure_prevents_all_effects(
    postgres_raw_db, monkeypatch, kind
):
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "aa" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, routine, change = await routine_proposal(kind)
        before = await postgres_raw_db.get("node", routine.id) if routine else None
        edges = await postgres_raw_db.find("edge", {}, sort=[("id", 1)])

        async def unavailable(**kwargs):
            raise RuntimeError("Injected failed routine receipt")

        monkeypatch.setattr(
            staging_execution, "record_staging_execution_result", unavailable
        )
        result = await staging_apply.bless_and_execute(
            user_id=auth.id, token=change.token
        )
        assert not result["consumed"], result
        if routine is None:
            assert await RoutineTask.find({"user_id": auth.id}) == []
        else:
            assert await postgres_raw_db.get("node", routine.id) == before
        assert await postgres_raw_db.find("edge", {}, sort=[("id", 1)]) == edges


@pytest.mark.parametrize("kind", KINDS)
async def test_routine_recovery_returns_current_state_without_repeating_edit(
    postgres_raw_db, monkeypatch, kind
):
    from app.agentive import staging

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "aa" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, _, change = await routine_proposal(kind)
        consume = staging_apply.consume_token

        async def unavailable(**kwargs):
            raise StagingError("completion_unavailable", "Injected lost completion")

        monkeypatch.setattr(staging_apply, "consume_token", unavailable)
        first = await staging_apply.bless_and_execute(
            user_id=auth.id, token=change.token
        )
        assert first["application_state"] == "reconciliation_required", first
        rows = await RoutineTask.find({"user_id": auth.id})
        assert len(rows) == 1
        row = rows[0]
        await routine_tasks.update_routine_task(
            user_id=auth.id,
            routine_id=row.id,
            instruction="Later authorized instruction",
        )
        before = await postgres_raw_db.get("node", row.id)
        monkeypatch.setattr(staging_apply, "consume_token", consume)
        staging._tokens.clear()
        second = await staging_apply.bless_and_execute(
            user_id=auth.id, token=change.token
        )
        assert second["consumed"], second
        assert second["execute_result"]["execution_receipt"]["replayed"] is True
        assert second["execute_result"]["instruction"] == "Later authorized instruction"
        assert await postgres_raw_db.get("node", row.id) == before
        assert len(await RoutineTask.find({"user_id": auth.id})) == 1


@pytest.mark.parametrize("kind", KINDS)
async def test_routine_foreign_thread_prevents_effect(
    postgres_raw_db, monkeypatch, kind
):
    from app.models.nodes import ChatThread

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "aa" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, routine, change = await routine_proposal(kind)
        thread_id = routine.thread_id if routine else change.payload["thread_id"]
        thread = await ChatThread.find_one({"id": thread_id})
        thread.user_id = "foreign"
        await thread.save()
        before = await postgres_raw_db.get("node", routine.id) if routine else None
        result = await staging_apply.bless_and_execute(
            user_id=auth.id, token=change.token
        )
        assert not result["consumed"], result
        if routine:
            assert await postgres_raw_db.get("node", routine.id) == before
        else:
            assert await RoutineTask.find({"user_id": auth.id}) == []


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("after_commit", [False, True])
async def test_routine_revocation_separates_effect_from_current_disclosure(
    postgres_raw_db, monkeypatch, kind, after_commit
):
    from app.agentive import staging
    from tests.contract.test_release_admission_postgres import remove_membership

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "aa" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, _, change = await routine_proposal(kind)
        consume = staging_apply.consume_token
        if after_commit:

            async def unavailable(**kwargs):
                raise StagingError("completion_unavailable", "Lost completion")

            monkeypatch.setattr(staging_apply, "consume_token", unavailable)
            first = await staging_apply.bless_and_execute(
                user_id=auth.id, token=change.token
            )
            assert first["application_state"] == "reconciliation_required", first
            monkeypatch.setattr(staging_apply, "consume_token", consume)
        await remove_membership(auth, workspace)
        rows_before = await postgres_raw_db.find(
            "node", {"entity": RoutineTask._entity_name()}, sort=[("id", 1)]
        )
        staging._tokens.clear()
        result = await staging_apply.bless_and_execute(
            user_id=auth.id, token=change.token
        )
        if after_commit:
            assert result["consumed"], result
            assert result["execute_result"]["result_access"] == "withheld", result
            assert "instruction" not in result["execute_result"]
        else:
            assert not result["consumed"], result
        assert (
            await postgres_raw_db.find(
                "node", {"entity": RoutineTask._entity_name()}, sort=[("id", 1)]
            )
            == rows_before
        )


@pytest.mark.parametrize("kind", KINDS)
async def test_cancellation_before_routine_receipt_rolls_back(
    postgres_raw_db, monkeypatch, kind
):
    import asyncio

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "aa" * 32)
    entered = asyncio.Event()
    record = staging_execution.record_staging_execution_result

    async def blocked(**kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(staging_execution, "record_staging_execution_result", blocked)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, routine, change = await routine_proposal(kind)
        before = await postgres_raw_db.get("node", routine.id) if routine else None
        executing = asyncio.create_task(
            staging_apply.bless_and_execute(user_id=auth.id, token=change.token)
        )
        try:
            await asyncio.wait_for(entered.wait(), 10)
        finally:
            executing.cancel()
            with pytest.raises(asyncio.CancelledError):
                await executing
        if routine:
            assert await postgres_raw_db.get("node", routine.id) == before
        else:
            assert await RoutineTask.find({"user_id": auth.id}) == []
        monkeypatch.setattr(
            staging_execution, "record_staging_execution_result", record
        )
        retried = await staging_apply.bless_and_execute(
            user_id=auth.id, token=change.token
        )
        assert retried["consumed"], retried
        assert len(await RoutineTask.find({"user_id": auth.id})) == 1


@pytest.mark.parametrize("kind", KINDS)
async def test_two_pools_apply_same_routine_approval_once(
    postgres_raw_db, monkeypatch, kind
):
    import asyncio
    from contextvars import Context

    from jvspatial.db.factory import create_database

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "aa" * 32)
    second = create_database(db_type="postgres", min_size=1, max_size=1)
    tasks = []
    try:
        async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
            auth, _, _, change = await routine_proposal(kind)
            for collection in ("node", "edge", "object"):
                await second.find(collection, {"id": ""}, limit=1)

            async def apply(database):
                async with scoped_default_context_async(
                    GraphContext(database=database)
                ):
                    return await staging_apply.bless_and_execute(
                        user_id=auth.id, token=change.token
                    )

            tasks = [
                asyncio.create_task(apply(db), context=Context())
                for db in (postgres_raw_db, second)
            ]
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True), 20
            )
            assert any(
                isinstance(r, dict) and r.get("consumed") for r in results
            ), results
            for result in results:
                if isinstance(result, Exception):
                    assert isinstance(result, StagingError), result
                    assert result.code in {
                        "already_executing",
                        "already_consumed",
                    }, result
            rows = await RoutineTask.find({"user_id": auth.id})
            assert len(rows) == 1
            events = await postgres_raw_db.find(
                "object",
                {
                    "context.event.resource_id": rows[0].id,
                    "context.event.action": kind.replace(
                        "routine_task_", "routine_task."
                    ),
                },
            )
            assert len(events) == 1
            record = await postgres_raw_db.get(
                "object", staging_execution.execution_record_id(change.token)
            )
            assert record["context"]["state"] == "applied"
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await second.close()


async def test_two_pools_merge_distinct_reviewed_routine_deltas(
    postgres_raw_db, monkeypatch
):
    import asyncio
    from contextvars import Context

    from jvspatial.db.factory import create_database

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "aa" * 32)
    second = create_database(db_type="postgres", min_size=1, max_size=1)
    tasks = []
    try:
        async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
            auth, workspace, routine, first = await routine_proposal(
                "routine_task_update"
            )
            routine.run_count = 3
            await routine.save()
            reset = current_scope_workspace_id.set(workspace.id)
            try:
                other = await create_staged_change(
                    user_id=auth.id,
                    session_id=None,
                    kind="routine_task_update",
                    summary="Edit instruction",
                    diff_human="One instruction",
                    diff_machine={},
                    payload={
                        "routine_id": routine.id,
                        "instruction": "Second reviewed instruction",
                    },
                )
            finally:
                current_scope_workspace_id.reset(reset)
            for collection in ("node", "edge", "object"):
                await second.find(collection, {"id": ""}, limit=1)

            async def apply(database, change):
                async with scoped_default_context_async(
                    GraphContext(database=database)
                ):
                    return await staging_apply.bless_and_execute(
                        user_id=auth.id, token=change.token
                    )

            tasks = [
                asyncio.create_task(apply(db, change), context=Context())
                for db, change in ((postgres_raw_db, first), (second, other))
            ]
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True), 20
            )
            assert all(
                isinstance(r, dict) and r.get("consumed") for r in results
            ), results
            row = await postgres_raw_db.get("node", routine.id)
            assert row["context"]["status"] == "paused"
            assert row["context"]["instruction"] == "Second reviewed instruction"
            assert row["context"]["run_count"] == 3
            events = await postgres_raw_db.find(
                "object",
                {
                    "context.event.resource_id": routine.id,
                    "context.event.action": "routine_task.update",
                },
            )
            assert len(events) == 2
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await second.close()


@pytest.mark.parametrize("kind", ["routine_task_cancel", "routine_task_purge"])
@pytest.mark.parametrize("unavailable", ["workspace", "thread"])
async def test_owner_can_stop_without_new_execution_authority(
    postgres_raw_db, monkeypatch, kind, unavailable
):
    from app.models.nodes import ChatThread
    from tests.contract.test_release_admission_postgres import remove_membership

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "aa" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, routine, change = await routine_proposal(kind)
        if unavailable == "workspace":
            await remove_membership(auth, workspace)
        else:
            thread = await ChatThread.find_one({"id": routine.thread_id})
            thread.archived = True
            await thread.save()
        result = await staging_apply.bless_and_execute(
            user_id=auth.id, token=change.token
        )
        assert result["consumed"], result
        row = await postgres_raw_db.get("node", routine.id)
        if kind == "routine_task_purge":
            assert row is None
        else:
            assert row["context"]["status"] == "cancelled"


@pytest.mark.parametrize("kind", ["routine_task_cancel", "routine_task_purge"])
async def test_failed_stop_receipt_never_calls_local_interrupt(
    postgres_raw_db, monkeypatch, kind
):
    from app.services.app_operations.transaction_scope import (
        require_outside_graph_transaction,
    )

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "aa" * 32)
    calls = []

    async def interrupted(routine):
        require_outside_graph_transaction()
        calls.append(routine.id)
        return True

    async def unavailable(**kwargs):
        raise RuntimeError("Synthetic receipt failure")

    monkeypatch.setattr(routine_tasks, "abort_routine_run", interrupted)
    original = staging_execution.record_staging_execution_result
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, routine, change = await routine_proposal(kind)
        before = await postgres_raw_db.get("node", routine.id)
        monkeypatch.setattr(
            staging_execution, "record_staging_execution_result", unavailable
        )
        first = await staging_apply.bless_and_execute(
            user_id=auth.id, token=change.token
        )
        assert not first["consumed"], first
        assert calls == []
        assert await postgres_raw_db.get("node", routine.id) == before
        monkeypatch.setattr(
            staging_execution, "record_staging_execution_result", original
        )
        second = await staging_apply.bless_and_execute(
            user_id=auth.id, token=change.token
        )
        assert second["consumed"], second
        assert calls == [routine.id]


@pytest.mark.parametrize("kind", ["routine_task_cancel", "routine_task_purge"])
async def test_cancelled_postcommit_interrupt_does_not_replay_stop(
    postgres_raw_db, monkeypatch, kind
):
    import asyncio

    from app.agentive import staging
    from app.services.app_operations.transaction_scope import (
        require_outside_graph_transaction,
    )

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "aa" * 32)
    entered = asyncio.Event()
    calls = []

    async def blocked(routine):
        require_outside_graph_transaction()
        calls.append(routine.id)
        entered.set()
        await asyncio.Event().wait()

    async def available(routine):
        calls.append(routine.id)
        return False

    monkeypatch.setattr(routine_tasks, "abort_routine_run", blocked)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, routine, change = await routine_proposal(kind)
        executing = asyncio.create_task(
            staging_apply.bless_and_execute(user_id=auth.id, token=change.token)
        )
        try:
            await asyncio.wait_for(entered.wait(), 10)
            receipt = await postgres_raw_db.get(
                "object", staging_execution.execution_record_id(change.token)
            )
            assert receipt["context"]["state"] == "applied"
        finally:
            executing.cancel()
            with pytest.raises(asyncio.CancelledError):
                await executing
        monkeypatch.setattr(routine_tasks, "abort_routine_run", available)
        staging._tokens.clear()
        recovered = await staging_apply.bless_and_execute(
            user_id=auth.id, token=change.token
        )
        assert recovered["consumed"], recovered
        assert recovered["execute_result"]["execution_receipt"]["replayed"] is True
        assert calls == [routine.id]
