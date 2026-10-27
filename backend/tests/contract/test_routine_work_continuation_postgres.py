"""A routine's leased authority follows its actual agent route and later effects."""

from types import SimpleNamespace

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.agentive.services import routine_tasks, work_execution, work_items
from app.schemas.agentive.work import WorkError
from tests.contract.test_staging_routine_commands_postgres import routine_proposal

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


async def claimed_routine():
    auth, workspace, routine, _ = await routine_proposal("routine_task_update")
    routine.delivery_mode = "agent"
    await routine.save()
    queued = await routine_tasks.enqueue_routine_turn_work(
        routine_id=routine.id,
        principal_id=auth.id,
        workspace_id=workspace.id,
        thread_id=routine.thread_id,
        scheduled_for="2026-10-16T09:00:00+00:00",
    )
    claimed = await work_items.claim_due_candidate(
        worker_id="routine-continuation-proof",
        work_item_id=queued.work_item_id,
        lease_seconds=120,
    )
    assert claimed is not None
    ctx = work_execution.build_work_execution_context(
        work_item=claimed, logical_step_key="provider:0"
    )
    return auth, routine, claimed, ctx


@pytest.mark.parametrize("removed", [False, True])
@pytest.mark.parametrize("boundary", ["preflight", "effect"])
async def test_stopped_routine_rejects_existing_worker_authority(
    postgres_raw_db, removed, boundary
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, routine, _, ctx = await claimed_routine()
        if removed:
            await routine_tasks.delete_routine_task(
                user_id=auth.id, routine_id=routine.id
            )
        else:
            await routine_tasks.cancel_routine_task(
                user_id=auth.id, routine_id=routine.id
            )
        with pytest.raises(WorkError) as stopped:
            if boundary == "preflight":
                await work_execution.assert_effect_boundary_allowed(ctx)
            else:
                async with work_items.authorized_work_item_effect(ctx):
                    pytest.fail("A stopped routine retained effect authority")
        assert stopped.value.code == "work.cancelled"


async def test_agent_route_receives_the_existing_routine_lease(
    postgres_raw_db, monkeypatch
):
    from app.api import ai_chat
    from app.services import chat_turn_registry
    from app.services.chat_sse import sse_bytes

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "aa" * 32)
    provider = SimpleNamespace(
        id="integral_native", label="Synthetic", is_available=lambda: True
    )
    monkeypatch.setattr(
        ai_chat, "get_registry", lambda: SimpleNamespace(get=lambda _: provider)
    )
    seen = []

    async def streamed(**kwargs):
        seen.append(kwargs["turn_ctx"])
        yield sse_bytes("turn-settled", {"status": "succeeded"})

    async def notify(*args, **kwargs):
        pass

    monkeypatch.setattr(ai_chat, "generate_chat_turn_sse", streamed)
    monkeypatch.setattr(ai_chat, "notify_thread_stream_update", notify)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        _, routine, claimed, ctx = await claimed_routine()
        try:
            await routine_tasks.execute_routine_turn_under_lease(claimed, ctx)
            assert len(seen) == 1
            assert seen[0].extra_data.get("work_execution_context") == ctx.model_dump(
                mode="json"
            )
            assert seen[0].extra_data["run_id"] == ctx.run_id
            from app.agentive.services.execution_runs import AgentRun

            await chat_turn_registry.release_turn(routine.thread_id)
            await routine_tasks.execute_routine_turn_under_lease(claimed, ctx)
            runs = await AgentRun.find({"run_id": ctx.run_id})
            assert len(runs) == 1
            assert runs[0].work_item_id == claimed.work_item_id
            assert len(seen) == 2
            runs[0].status = "succeeded"
            await runs[0].save()
            await chat_turn_registry.release_turn(routine.thread_id)
            with pytest.raises(WorkError):
                await routine_tasks.execute_routine_turn_under_lease(claimed, ctx)
            assert len(seen) == 2, "A terminal prior run restarted the model stream"
        finally:
            await chat_turn_registry.release_turn(routine.thread_id)


async def test_worker_settles_routine_before_releasing_chat_and_notifying(
    postgres_raw_db, monkeypatch
):
    """A completed leased run must unlock its chat and notify the live browser."""
    from app.agentive.services import work_worker
    from app.api import ai_chat
    from app.services import chat_turn_registry
    from app.services.chat_sse import sse_bytes

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "aa" * 32)
    provider = SimpleNamespace(
        id="integral_native", label="Synthetic", is_available=lambda: True
    )
    monkeypatch.setattr(
        ai_chat, "get_registry", lambda: SimpleNamespace(get=lambda _: provider)
    )
    events = []

    async def streamed(**kwargs):
        handle = chat_turn_registry.get_turn(kwargs["thread"].id)
        assert handle.work_lease_fence == claimed.lease_fence
        yield sse_bytes("turn-settled", {"status": "succeeded"})

    async def notify(user_id, thread_id, **kwargs):
        if kwargs["status"] == "completed":
            from app.agentive.work_models import WorkItem

            current = await WorkItem.find_one({"work_item_id": claimed.work_item_id})
            assert current.status == "succeeded"
            assert chat_turn_registry.get_turn(thread_id) is None
        events.append(kwargs)

    monkeypatch.setattr(ai_chat, "generate_chat_turn_sse", streamed)
    monkeypatch.setattr(ai_chat, "notify_thread_stream_update", notify)
    monkeypatch.setattr(
        "app.services.chat_thread_events.notify_thread_stream_update", notify
    )
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        _, routine, claimed, _ = await claimed_routine()
        try:
            done = await work_worker.execute_claimed_work(claimed)
            assert done.status == "succeeded"
            assert [event["status"] for event in events] == ["started", "completed"]
            assert events[0]["turn_id"] == events[1]["turn_id"]
        finally:
            await chat_turn_registry.release_turn(routine.thread_id)


@pytest.mark.parametrize("removed", [False, True])
async def test_other_pool_stop_is_observed_by_the_running_worker(
    postgres_raw_db, removed
):
    from jvspatial.db.factory import create_database

    other = create_database(db_type="postgres", min_size=1, max_size=1)
    try:
        async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
            auth, routine, _, ctx = await claimed_routine()
            async with scoped_default_context_async(GraphContext(database=other)):
                if removed:
                    await routine_tasks.delete_routine_task(
                        user_id=auth.id, routine_id=routine.id
                    )
                else:
                    await routine_tasks.cancel_routine_task(
                        user_id=auth.id, routine_id=routine.id
                    )
            with pytest.raises(WorkError) as refused:
                async with work_items.authorized_work_item_effect(ctx):
                    pytest.fail("Another worker's committed stop was ignored")
            assert refused.value.code == "work.cancelled"
    finally:
        await other.close()


async def test_inflight_effect_commits_before_stop_then_future_effect_is_refused(
    postgres_raw_db,
):
    import asyncio
    from contextvars import Context

    from jvspatial.db.factory import create_database

    other = create_database(db_type="postgres", min_size=1, max_size=1)
    entered = asyncio.Event()
    release = asyncio.Event()
    stopping = asyncio.Event()
    tasks = []
    try:
        async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
            auth, routine, _, ctx = await claimed_routine()
            witness = "routine-effect-proof-" + routine.id

            async def effect():
                async with scoped_default_context_async(
                    GraphContext(database=postgres_raw_db)
                ):
                    async with work_items.authorized_work_item_effect(ctx) as graph:
                        entered.set()
                        await release.wait()
                        await graph.database.insert_if_absent(
                            "object",
                            {
                                "id": witness,
                                "entity": "RoutineEffectProof",
                                "context": {"committed": True},
                            },
                        )

            async def stop():
                async with scoped_default_context_async(GraphContext(database=other)):
                    stopping.set()
                    await routine_tasks.cancel_routine_task(
                        user_id=auth.id, routine_id=routine.id
                    )

            tasks.append(asyncio.create_task(effect(), context=Context()))
            await asyncio.wait_for(entered.wait(), 10)
            tasks.append(asyncio.create_task(stop(), context=Context()))
            await asyncio.wait_for(stopping.wait(), 10)
            await asyncio.sleep(0.05)
            assert not tasks[1].done(), "Stop bypassed the held effect row fence"
            release.set()
            await asyncio.wait_for(asyncio.gather(*tasks), 10)
            assert await postgres_raw_db.get("object", witness) is not None
            with pytest.raises(WorkError) as refused:
                async with work_items.authorized_work_item_effect(ctx):
                    pytest.fail("Stopped work retained effect authority")
            assert refused.value.code == "work.cancelled"
    finally:
        release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await other.close()


@pytest.mark.parametrize("removed", [False, True])
async def test_stopped_lease_is_terminalized_without_resuming_model(
    postgres_raw_db, monkeypatch, removed
):
    from app.agentive.services import work_worker
    from app.agentive.work_models import WorkItem
    from app.services import routine_task_scheduler as scheduler

    async def forbidden(task):
        pytest.fail("Stopped work restarted an agent turn")

    monkeypatch.setattr(scheduler, "_run_agent_turn", forbidden)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, routine, claimed, _ = await claimed_routine()
        if removed:
            await routine_tasks.delete_routine_task(
                user_id=auth.id, routine_id=routine.id
            )
        else:
            await routine_tasks.cancel_routine_task(
                user_id=auth.id, routine_id=routine.id
            )
        done = await work_worker.execute_claimed_work(claimed)
        assert done.status == "cancelled"
        assert done.failure["code"] == "work.cancelled"
        persisted = await WorkItem.find_one({"work_item_id": claimed.work_item_id})
        assert persisted.status == "cancelled"
        assert persisted.lease_token == ""


async def test_client_body_cannot_supply_worker_authority(postgres_raw_db, monkeypatch):
    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api import ai_chat
    from app.services import chat_turn_registry
    from app.services.chat_sse import settled_chat_turn_outcome, sse_bytes

    provider = SimpleNamespace(
        id="integral_native", label="Synthetic", is_available=lambda: True
    )
    monkeypatch.setattr(
        ai_chat, "get_registry", lambda: SimpleNamespace(get=lambda _: provider)
    )
    seen = []

    async def streamed(**kwargs):
        seen.append(kwargs["turn_ctx"])
        yield sse_bytes("turn-settled", {"status": "succeeded"})

    async def notify(*args, **kwargs):
        pass

    monkeypatch.setattr(ai_chat, "generate_chat_turn_sse", streamed)
    monkeypatch.setattr(ai_chat, "notify_thread_stream_update", notify)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, routine, _, ctx = await claimed_routine()
        try:
            response = await invoke_route_in_process(
                ai_chat.agent_turn,
                principal_id=auth.id,
                scope=routine.workspace_id,
                thread_id=routine.thread_id,
                prompt="Synthetic request",
                json_body={
                    "origin": "routine_task",
                    "work_execution_context": ctx.model_dump(mode="json"),
                },
            )
            assert await settled_chat_turn_outcome(response.body_iterator) == (
                True,
                None,
            )
            assert len(seen) == 1
            assert "work_execution_context" not in seen[0].extra_data
            assert seen[0].extra_data["run_id"] != ctx.run_id
        finally:
            await chat_turn_registry.release_turn(routine.thread_id)
