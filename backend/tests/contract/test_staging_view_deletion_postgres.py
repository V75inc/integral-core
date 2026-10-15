"""Next recovery increment: standalone View deletion, using native Core fixtures."""

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.agentive import staging
from app.agentive.services import staging_apply, staging_execution
from app.agentive.staging import StagingError, create_staged_change
from app.services.agent_scope import current_scope_workspace_id
from tests.contract.test_staging_saved_view_postgres import view_proposal

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def receipt_key(monkeypatch):
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "88" * 32)


async def deletion_proposal():
    auth, workspace, track, existing = await view_proposal(update=True)
    reset = current_scope_workspace_id.set(workspace.id)
    try:
        sc = await create_staged_change(
            user_id=auth.id,
            session_id=None,
            kind="delete_view",
            summary="Delete saved view",
            diff_human="Delete one saved view",
            diff_machine={},
            payload={"view_id": existing.payload["view_id"]},
        )
    finally:
        current_scope_workspace_id.reset(reset)
    return auth, workspace, track, sc


async def test_standalone_view_deletion_completion_loss_recovers_without_replay(
    postgres_raw_db, monkeypatch
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, _, sc = await deletion_proposal()
        consume = staging_apply.consume_token

        async def unavailable(**kwargs):
            raise StagingError("completion_unavailable", "Injected completion loss")

        monkeypatch.setattr(staging_apply, "consume_token", unavailable)
        first = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert first.get("application_state") == "reconciliation_required", first
        record = await postgres_raw_db.get(
            "object", staging_execution.execution_record_id(sc.token)
        )
        assert record is not None and record["context"]["state"] == "applied", record
        assert await postgres_raw_db.get("node", sc.payload["view_id"]) is None
        nodes = await postgres_raw_db.find("node", {}, sort=[("id", 1)])
        edges = await postgres_raw_db.find("edge", {}, sort=[("id", 1)])
        staging._tokens.clear()
        monkeypatch.setattr(staging_apply, "consume_token", consume)
        recovered = await staging_apply.bless_and_execute(
            user_id=auth.id, token=sc.token
        )
        assert recovered["consumed"] is True, recovered
        result = recovered["execute_result"]
        assert result["deleted"] is True
        assert result["execution_receipt"]["replayed"] is True
        assert sc.payload["view_id"] not in str(result)
        assert await postgres_raw_db.find("node", {}, sort=[("id", 1)]) == nodes
        assert await postgres_raw_db.find("edge", {}, sort=[("id", 1)]) == edges


async def test_standalone_view_deletion_receipt_failure_rolls_back(
    postgres_raw_db, monkeypatch
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, _, sc = await deletion_proposal()
        nodes = await postgres_raw_db.find("node", {}, sort=[("id", 1)])
        edges = await postgres_raw_db.find("edge", {}, sort=[("id", 1)])

        async def unavailable(**kwargs):
            raise OSError("Injected receipt failure")

        monkeypatch.setattr(
            staging_execution, "record_staging_execution_result", unavailable
        )
        result = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert result["consumed"] is False, result
        assert result["execute_result"]["error"], result
        assert await postgres_raw_db.find("node", {}, sort=[("id", 1)]) == nodes
        assert await postgres_raw_db.find("edge", {}, sort=[("id", 1)]) == edges


@pytest.mark.parametrize("dispatch_kind", ["staged", "http"])
async def test_standalone_view_delete_revocation_waits_for_admitted_effect(
    postgres_raw_db, monkeypatch, dispatch_kind
):
    import asyncio
    from contextvars import Context

    from jvspatial.db.factory import create_database

    from app.models.nodes import View
    from app.services.membership_write_scope import membership_write_scope
    from tests.contract.test_release_admission_postgres import remove_membership

    second = create_database(db_type="postgres", min_size=1, max_size=1)
    writing = revoking = None
    entered, finish = asyncio.Event(), asyncio.Event()
    delete = View.delete

    async def hold(self, *args, **kwargs):
        entered.set()
        await finish.wait()
        return await delete(self, *args, **kwargs)

    try:
        async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
            auth, workspace, _, sc = await deletion_proposal()
            async with membership_write_scope(workspace.id, principal_ids=(auth.id,)):
                pass
            for collection in ("node", "edge", "object"):
                await second.find(collection, {"id": ""}, limit=1)
            monkeypatch.setattr(View, "delete", hold)

            async def apply():
                async with scoped_default_context_async(GraphContext(database=second)):
                    if dispatch_kind == "http":
                        from app.agentive.staging_executors import _call_endpoint
                        from app.api.views import delete_view

                        reset = current_scope_workspace_id.set(workspace.id)
                        try:
                            return await _call_endpoint(
                                delete_view, auth.id, view_id=sc.payload["view_id"]
                            )
                        finally:
                            current_scope_workspace_id.reset(reset)
                    return await staging_apply.bless_and_execute(
                        user_id=auth.id, token=sc.token
                    )

            async def revoke():
                async with scoped_default_context_async(
                    GraphContext(database=postgres_raw_db)
                ):
                    await remove_membership(auth, workspace)

            writing = asyncio.create_task(apply(), context=Context())
            await asyncio.wait_for(entered.wait(), timeout=15)
            revoking = asyncio.create_task(revoke(), context=Context())
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(revoking), timeout=0.15)
            finish.set()
            result = await asyncio.wait_for(writing, timeout=15)
            if dispatch_kind == "http":
                assert result["deleted_view_id"] == sc.payload["view_id"], result
            else:
                assert result["consumed"] is True, result
            await asyncio.wait_for(revoking, timeout=15)
    finally:
        finish.set()
        tasks = [task for task in (writing, revoking) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await second.close()


@pytest.mark.parametrize("dispatch_kind", ["staged", "http"])
async def test_view_deletion_delivers_only_committed_event_and_receipt(
    postgres_raw_db, monkeypatch, dispatch_kind
):
    from app.services import workspace_membership
    from app.services.app_operations.event_outbox import event_outbox_object_id
    from app.services.app_operations.transaction_scope import (
        OperationTransactionUnavailable,
        current_graph_transaction_database,
    )

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, _, sc = await deletion_proposal()
        offered = []

        async def deliver(outbox):
            with pytest.raises(OperationTransactionUnavailable):
                current_graph_transaction_database()
            assert (
                await postgres_raw_db.get("object", event_outbox_object_id(outbox))
                is not None
            )
            receipt = await postgres_raw_db.get(
                "object", staging_execution.execution_record_id(sc.token)
            )
            if dispatch_kind == "staged":
                assert receipt["context"]["state"] == "applied"
            else:
                assert receipt is None
            assert await postgres_raw_db.get("node", sc.payload["view_id"]) is None
            offered.append(outbox)

        monkeypatch.setattr(workspace_membership, "_deliver", deliver)
        if dispatch_kind == "http":
            from app.agentive.staging_executors import _call_endpoint
            from app.api.views import delete_view

            reset = current_scope_workspace_id.set(workspace.id)
            try:
                result = await _call_endpoint(
                    delete_view, auth.id, view_id=sc.payload["view_id"]
                )
            finally:
                current_scope_workspace_id.reset(reset)
            assert result["deleted_view_id"] == sc.payload["view_id"], result
        else:
            result = await staging_apply.bless_and_execute(
                user_id=auth.id, token=sc.token
            )
            assert result["consumed"] is True, result
        assert len(offered) == 1


async def test_view_deletion_malformed_result_rolls_back_effect_and_event(
    postgres_raw_db, monkeypatch
):
    from app.api import views

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, _, sc = await deletion_proposal()
        nodes = await postgres_raw_db.find("node", {}, sort=[("id", 1)])
        edges = await postgres_raw_db.find("edge", {}, sort=[("id", 1)])
        events = await postgres_raw_db.find(
            "object", {"entity": "OperationEventOutbox"}, sort=[("id", 1)]
        )
        endpoint = views._delete_view_after_admission

        async def unexpected(*args, **kwargs):
            result = await endpoint(*args, **kwargs)
            assert not result.get("error"), result
            return {**result, "deleted_view_id": "n.View.unexpected"}

        monkeypatch.setattr(views, "_delete_view_after_admission", unexpected)
        result = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert result["consumed"] is False, result
        assert (
            result["execute_result"]["error_code"] == "execution_result_invalid"
        ), result
        assert await postgres_raw_db.find("node", {}, sort=[("id", 1)]) == nodes
        assert await postgres_raw_db.find("edge", {}, sort=[("id", 1)]) == edges
        assert (
            await postgres_raw_db.find(
                "object", {"entity": "OperationEventOutbox"}, sort=[("id", 1)]
            )
            == events
        )


async def test_view_deletion_recovery_after_revocation_is_opaque(
    postgres_raw_db, monkeypatch
):
    from tests.contract.test_release_admission_postgres import remove_membership

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, _, sc = await deletion_proposal()
        consume = staging_apply.consume_token

        async def unavailable(**kwargs):
            raise StagingError("completion_unavailable", "Injected completion loss")

        monkeypatch.setattr(staging_apply, "consume_token", unavailable)
        first = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert first.get("application_state") == "reconciliation_required", first
        await remove_membership(auth, workspace)
        nodes = await postgres_raw_db.find("node", {}, sort=[("id", 1)])
        staging._tokens.clear()
        monkeypatch.setattr(staging_apply, "consume_token", consume)
        result = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert result["consumed"] is True, result
        recovered = result["execute_result"]
        assert recovered["deleted"] is True
        assert recovered["result_access"] == "withheld"
        assert sc.payload["view_id"] not in str(recovered)
        assert "Original view" not in str(recovered)
        assert await postgres_raw_db.find("node", {}, sort=[("id", 1)]) == nodes


async def test_view_deletion_recovery_never_reads_reused_view_identity(
    postgres_raw_db, monkeypatch
):
    from app.agentive.services.staging_command_apply import _readback_view
    from app.services import resource_location

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, track, sc = await deletion_proposal()
        result = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert result["consumed"] is True, result

        async def forbidden_read(*args, **kwargs):
            raise AssertionError(
                "A deletion receipt must not project a reused identity"
            )

        monkeypatch.setattr(resource_location, "locate_resource", forbidden_read)
        recovered = await _readback_view(
            sc,
            workspace.id,
            {
                "view_id": sc.payload["view_id"],
                "track_id": track.id,
                "deleted": True,
            },
        )
        assert recovered["deleted"] is True
        assert recovered["result_access"] == "withheld"
        assert sc.payload["view_id"] not in str(recovered)


@pytest.mark.parametrize("interrupted_step", [1, 2, 3])
async def test_save_delete_view_batch_recovers_after_child_or_parent_completion_loss(
    postgres_raw_db, monkeypatch, interrupted_step
):
    from app.agentive.services import staging_command_batch

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, track, _ = await deletion_proposal()
        reset = current_scope_workspace_id.set(workspace.id)
        try:
            sc = await create_staged_change(
                user_id=auth.id,
                session_id=None,
                kind="batch",
                summary="Create and remove View",
                diff_human="Two approved View changes",
                diff_machine={},
                payload={
                    "operations": [
                        {
                            "kind": "save_view",
                            "payload": {"track_id": track.id, "name": "Temporary view"},
                        },
                        {
                            "kind": "delete_view",
                            "payload": {"view_id": "{{view.id:Temporary view}}"},
                        },
                    ]
                },
            )
        finally:
            current_scope_workspace_id.reset(reset)
        apply = staging_command_batch.apply_staged_command_once
        consume = staging_apply.consume_token
        calls = 0

        async def lost_worker(**kwargs):
            nonlocal calls
            result = await apply(**kwargs)
            assert not result.get("error"), result
            calls += 1
            if calls == interrupted_step:
                raise RuntimeError("Lost View batch worker")
            return result

        async def lost_completion(**kwargs):
            raise StagingError("completion_unavailable", "Lost View batch completion")

        if interrupted_step < 3:
            monkeypatch.setattr(
                staging_command_batch, "apply_staged_command_once", lost_worker
            )
            with pytest.raises(RuntimeError, match="Lost View batch worker"):
                await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        else:
            monkeypatch.setattr(staging_apply, "consume_token", lost_completion)
            first = await staging_apply.bless_and_execute(
                user_id=auth.id, token=sc.token
            )
            assert first.get("application_state") == "reconciliation_required", first
        staging._tokens.clear()
        monkeypatch.setattr(staging_command_batch, "apply_staged_command_once", apply)
        monkeypatch.setattr(staging_apply, "consume_token", consume)
        recovered = await staging_apply.bless_and_execute(
            user_id=auth.id, token=sc.token
        )
        assert recovered["consumed"] is True, recovered
        assert recovered["execute_result"]["completed"] == 2, recovered
        assert not await postgres_raw_db.find(
            "node", {"entity": "View", "context.name": "Temporary view"}
        )
        for index in range(2):
            receipt = await postgres_raw_db.get(
                "object",
                staging_execution.execution_record_id(f"{sc.token}:step:{index}"),
            )
            assert receipt["context"]["state"] == "applied"


async def test_view_deletion_rejects_changed_selected_view_after_approval(
    postgres_raw_db, monkeypatch
):
    from app.services.agent_insights import save_view

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, track, sc = await deletion_proposal()
        reset = current_scope_workspace_id.set(workspace.id)
        try:
            other = await save_view(
                user_id=auth.id, track_id=track.id, name="Unselected View"
            )
            assert not other.get("error"), other
        finally:
            current_scope_workspace_id.reset(reset)
        nodes = await postgres_raw_db.find("node", {}, sort=[("id", 1)])
        edges = await postgres_raw_db.find("edge", {}, sort=[("id", 1)])
        dispatch = staging_apply._dispatch_kind

        async def redirected(**kwargs):
            kwargs["payload"] = {**kwargs["payload"], "view_id": other["view_id"]}
            return await dispatch(**kwargs)

        monkeypatch.setattr(staging_apply, "_dispatch_kind", redirected)
        result = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert result["consumed"] is False, result
        assert result["execute_result"]["error_code"] == "approval_unavailable", result
        assert await postgres_raw_db.find("node", {}, sort=[("id", 1)]) == nodes
        assert await postgres_raw_db.find("edge", {}, sort=[("id", 1)]) == edges


async def test_view_deletion_denies_revoked_membership_before_effect(postgres_raw_db):
    from tests.contract.test_release_admission_postgres import remove_membership

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, _, sc = await deletion_proposal()
        await remove_membership(auth, workspace)
        nodes = await postgres_raw_db.find("node", {}, sort=[("id", 1)])
        edges = await postgres_raw_db.find("edge", {}, sort=[("id", 1)])
        result = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert result["consumed"] is False, result
        assert result["execute_result"]["error"], result
        assert await postgres_raw_db.find("node", {}, sort=[("id", 1)]) == nodes
        assert await postgres_raw_db.find("edge", {}, sort=[("id", 1)]) == edges


async def test_http_view_deletion_outbox_failure_rolls_back_graph(
    postgres_raw_db, monkeypatch
):
    from app.agentive.staging_executors import _call_endpoint
    from app.api.views import delete_view
    from app.services import resource_events

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, _, sc = await deletion_proposal()
        nodes = await postgres_raw_db.find("node", {}, sort=[("id", 1)])
        edges = await postgres_raw_db.find("edge", {}, sort=[("id", 1)])

        async def unavailable(*args, **kwargs):
            raise OSError("Injected outbox failure")

        monkeypatch.setattr(resource_events, "queue_resource_event", unavailable)
        reset = current_scope_workspace_id.set(workspace.id)
        try:
            result = await _call_endpoint(
                delete_view, auth.id, view_id=sc.payload["view_id"]
            )
        finally:
            current_scope_workspace_id.reset(reset)
        assert result["error"], result
        assert await postgres_raw_db.find("node", {}, sort=[("id", 1)]) == nodes
        assert await postgres_raw_db.find("edge", {}, sort=[("id", 1)]) == edges
