"""Saved-view approvals commit their graph and recover through exact receipts."""

import asyncio
from contextvars import Context

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async
from jvspatial.db.factory import create_database

from app.agentive import staging
from app.agentive.services import staging_apply, staging_execution
from app.agentive.staging import StagingError, create_staged_change
from app.models.nodes import View
from app.services.agent_insights import save_view
from app.services.agent_scope import current_scope_workspace_id
from app.services.app_graph import (
    ensure_track_attached_operational_model,
    get_track_attached_operational_model,
)
from tests.contract.test_staging_command_apply_postgres import proposal

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def receipt_key(monkeypatch):
    """Use a test-only key for encrypted approved effect evidence."""
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "66" * 32)


async def view_proposal(*, update):
    """Prepare an attached Track model and a new or existing saved View."""
    auth, workspace, track, _ = await proposal()
    await ensure_track_attached_operational_model(track)
    payload = {"track_id": track.id, "name": "Approved view", "view_type": "feed"}
    reset = current_scope_workspace_id.set(workspace.id)
    try:
        if update:
            original = await save_view(
                user_id=auth.id, track_id=track.id, name="Original view"
            )
            assert not original.get("error"), original
            payload["view_id"] = original["view_id"]
        sc = await create_staged_change(
            user_id=auth.id,
            session_id=None,
            kind="save_view",
            summary="Save a view",
            diff_human="Save the approved view",
            diff_machine={},
            payload=payload,
        )
    finally:
        current_scope_workspace_id.reset(reset)
    return auth, workspace, track, sc


@pytest.mark.parametrize("update", [False, True])
async def test_view_completion_loss_recovers_without_duplicate_effect(
    postgres_raw_db, monkeypatch, update
):
    """Restart after commit uses the receipt and a current authorized read."""
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, _, sc = await view_proposal(update=update)
        consume = staging_apply.consume_token

        async def unavailable(**kwargs):
            raise StagingError("completion_unavailable", "Lost completion")

        monkeypatch.setattr(staging_apply, "consume_token", unavailable)
        first = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert first["application_state"] == "reconciliation_required", first
        record = await postgres_raw_db.get(
            "object", staging_execution.execution_record_id(sc.token)
        )
        assert record is not None and record["context"]["state"] == "applied"
        views = await View.find({"name": "Approved view"})
        assert len(views) == 1
        view_id = views[0].id
        before = await postgres_raw_db.get("node", view_id)
        staging._tokens.clear()
        monkeypatch.setattr(staging_apply, "consume_token", consume)
        recovered = await staging_apply.bless_and_execute(
            user_id=auth.id, token=sc.token
        )
        assert recovered["consumed"] is True, recovered
        result = recovered["execute_result"]
        assert result["view"]["id"] == view_id
        assert result["execution_receipt"]["replayed"] is True
        assert await postgres_raw_db.get("node", view_id) == before
        assert len(await View.find({"name": "Approved view"})) == 1
        assert current_scope_workspace_id.get() is None


@pytest.mark.parametrize("update", [False, True])
async def test_view_receipt_failure_rolls_back_graph(
    postgres_raw_db, monkeypatch, update
):
    """Receipt storage failure cannot leave a created or half-updated View."""
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, track, sc = await view_proposal(update=update)
        model = await get_track_attached_operational_model(track)
        before_model = await postgres_raw_db.get("node", model.id)
        before_views = await postgres_raw_db.find(
            "node", {"entity": View._entity_name()}
        )
        before_edges = await postgres_raw_db.find("edge", {}, sort=[("id", 1)])
        before_events = await postgres_raw_db.find(
            "object", {"entity": "OperationEventOutbox"}, sort=[("id", 1)]
        )
        original = (
            await postgres_raw_db.get("node", sc.payload["view_id"]) if update else None
        )

        async def unavailable(**kwargs):
            raise RuntimeError("Lost receipt storage")

        monkeypatch.setattr(
            staging_execution, "record_staging_execution_result", unavailable
        )
        result = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert result["consumed"] is False, result
        assert result["execute_result"]["error"], result
        assert (
            await postgres_raw_db.find("node", {"entity": View._entity_name()})
            == before_views
        )
        assert await postgres_raw_db.get("node", model.id) == before_model
        assert await postgres_raw_db.find("edge", {}, sort=[("id", 1)]) == before_edges
        assert (
            await postgres_raw_db.find(
                "object", {"entity": "OperationEventOutbox"}, sort=[("id", 1)]
            )
            == before_events
        )
        if update:
            assert await postgres_raw_db.get("node", sc.payload["view_id"]) == original


async def test_view_recovery_withholds_after_workspace_revocation(postgres_raw_db):
    """A receipt proves application, never current access to the saved View."""
    from app.agentive.services.staging_command_apply import _readback_view
    from tests.contract.test_release_admission_postgres import remove_membership

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, track, sc = await view_proposal(update=False)
        first = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert first["consumed"] is True, first
        view_id = first["execute_result"]["view_id"]
        before = await postgres_raw_db.get("node", view_id)
        await remove_membership(auth, workspace)
        result = await _readback_view(
            sc, workspace.id, {"view_id": view_id, "track_id": track.id}
        )
        assert result["applied"] is True
        assert result["result_access"] == "withheld"
        assert view_id not in str(result) and track.id not in str(result)
        assert "Approved view" not in str(result)
        assert await postgres_raw_db.get("node", view_id) == before
        assert current_scope_workspace_id.get() is None


async def test_view_recovery_does_not_hide_endpoint_authorization_outage(
    postgres_raw_db, monkeypatch
):
    """An unavailable read is not a completed authorization denial."""
    from app.agentive.services.staging_command_apply import _readback_view
    from app.api import views
    from app.exceptions import AuthorizationUnavailableError

    async def unavailable(*args, **kwargs):
        raise AuthorizationUnavailableError()

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, track, sc = await view_proposal(update=False)
        first = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert first["consumed"] is True, first
        monkeypatch.setattr(views, "get_view", unavailable)
        with pytest.raises(AuthorizationUnavailableError):
            await _readback_view(
                sc,
                workspace.id,
                {"view_id": first["execute_result"]["view_id"], "track_id": track.id},
            )
        assert current_scope_workspace_id.get() is None


@pytest.mark.parametrize(
    "field, value",
    [
        ("name", "Unapproved"),
        ("config", {"hidden": True}),
        ("view_type", "table"),
        ("view_id", "unapproved-view"),
    ],
)
async def test_view_effect_refuses_changed_approved_inputs(
    postgres_raw_db, field, value
):
    """The effect fence checks every View input, not just the Track ID."""
    from app.agentive.services.staging_command_apply import apply_staged_command_once
    from app.agentive.staging_executors import _x_save_view

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, _, sc = await view_proposal(update=False)
        await staging.bless_token(user_id=auth.id, token=sc.token)

        async def altered():
            return await _x_save_view(auth.id, {**sc.payload, field: value})

        with pytest.raises(StagingError) as exc:
            await apply_staged_command_once(sc=sc, request=None, execute=altered)
        assert exc.value.code == "approval_unavailable"
        assert await View.find({"name": "Approved view"}) == []


async def test_independent_identity_disable_precedes_saved_view_effect(postgres_raw_db):
    """A worker must wait for an earlier identity change, then deny the write."""
    from app.services.app_operations.transaction_scope import postgres_graph_transaction

    second = create_database(db_type="postgres", min_size=1, max_size=1)
    task = None
    try:
        async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
            auth, _, _, sc = await view_proposal(update=False)
            for collection in ("node", "edge", "object"):
                await second.find(collection, {"id": ""}, limit=1)
            entered = asyncio.Event()

            async def apply():
                async with scoped_default_context_async(GraphContext(database=second)):
                    entered.set()
                    return await staging_apply.bless_and_execute(
                        user_id=auth.id, token=sc.token
                    )

            async with postgres_graph_transaction(postgres_raw_db) as transaction:
                await transaction.find_one_and_update(
                    "object", {"id": auth.id}, {"$set": {"context.is_active": False}}
                )
                task = asyncio.create_task(apply(), context=Context())
                await asyncio.wait_for(entered.wait(), timeout=10)
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(asyncio.shield(task), timeout=0.15)
            result = await asyncio.wait_for(task, timeout=15)
            assert result["consumed"] is False, result
            assert result["execute_result"]["error"], result
            assert (
                await postgres_raw_db.find(
                    "node",
                    {"entity": View._entity_name(), "context.name": "Approved view"},
                )
                == []
            )
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await second.close()


@pytest.mark.parametrize("update", [False, True])
async def test_saved_view_event_outbox_commits_before_delivery(
    postgres_raw_db, monkeypatch, update
):
    """The saved View event must never be delivered from inside its graph transaction."""
    from app.services import workspace_membership
    from app.services.app_operations.event_outbox import event_outbox_object_id
    from app.services.app_operations.transaction_scope import (
        OperationTransactionUnavailable,
        current_graph_transaction_database,
    )

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, _, sc = await view_proposal(update=update)
        offered = []

        async def deliver(outbox):
            with pytest.raises(OperationTransactionUnavailable):
                current_graph_transaction_database()
            row = await postgres_raw_db.get("object", event_outbox_object_id(outbox))
            assert row is not None
            receipt = await postgres_raw_db.get(
                "object", staging_execution.execution_record_id(sc.token)
            )
            assert receipt["context"]["state"] == "applied"
            offered.append(outbox)

        monkeypatch.setattr(workspace_membership, "_deliver", deliver)
        result = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert result["consumed"] is True, result
        assert len(offered) == 1


async def test_workspace_revocation_waits_for_admitted_saved_view_effect(
    postgres_raw_db, monkeypatch
):
    """Revocation cannot commit between View authority admission and its write."""
    from app.services import app_graph
    from app.services.membership_write_scope import membership_write_scope
    from tests.contract.test_release_admission_postgres import remove_membership

    second = create_database(db_type="postgres", min_size=1, max_size=1)
    writing = revoking = None
    admitted, finish = asyncio.Event(), asyncio.Event()
    create_view = app_graph.create_track_view

    async def hold(*args, **kwargs):
        admitted.set()
        await finish.wait()
        return await create_view(*args, **kwargs)

    try:
        async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
            auth, workspace, _, sc = await view_proposal(update=False)
            # Prewarm authorization state/indexes so DDL cannot masquerade as
            # a resource authority lock in the competing transaction.
            async with membership_write_scope(workspace.id, principal_ids=(auth.id,)):
                pass
            for collection in ("node", "edge", "object"):
                await second.find(collection, {"id": ""}, limit=1)
            monkeypatch.setattr(app_graph, "create_track_view", hold)

            async def apply():
                async with scoped_default_context_async(GraphContext(database=second)):
                    return await staging_apply.bless_and_execute(
                        user_id=auth.id, token=sc.token
                    )

            async def revoke():
                async with scoped_default_context_async(
                    GraphContext(database=postgres_raw_db)
                ):
                    await remove_membership(auth, workspace)

            writing = asyncio.create_task(apply(), context=Context())
            await asyncio.wait_for(admitted.wait(), timeout=15)
            revoking = asyncio.create_task(revoke(), context=Context())
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(revoking), timeout=0.15)
            finish.set()
            result = await asyncio.wait_for(writing, timeout=15)
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


@pytest.mark.parametrize("update", [False, True])
async def test_saved_view_invalid_config_preserves_error_and_rolls_back(
    postgres_raw_db, update
):
    """Refused graph configuration must retain its 400 response without writes."""
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, _, original = await view_proposal(update=update)
        await staging.revoke_token(user_id=auth.id, token=original.token)
        reset = current_scope_workspace_id.set(workspace.id)
        try:
            sc = await create_staged_change(
                user_id=auth.id,
                session_id=None,
                kind="save_view",
                summary="Invalid view",
                diff_human="Invalid columns",
                diff_machine={},
                payload={
                    **original.payload,
                    "view_type": "kanban",
                    "config": {"kanban_columns": ["invalid-column"]},
                },
            )
        finally:
            current_scope_workspace_id.reset(reset)
        nodes = await postgres_raw_db.find("node", {}, sort=[("id", 1)])
        edges = await postgres_raw_db.find("edge", {}, sort=[("id", 1)])
        result = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert result["consumed"] is False, result
        assert result["execute_result"]["status_code"] == 400, result
        assert await postgres_raw_db.find("node", {}, sort=[("id", 1)]) == nodes
        assert await postgres_raw_db.find("edge", {}, sort=[("id", 1)]) == edges
        assert (
            await postgres_raw_db.find("object", {"entity": "OperationEventOutbox"})
            == []
        )


@pytest.mark.parametrize("lost_step", [1, 3])
async def test_saved_views_share_stable_mixed_batch_receipts(
    postgres_raw_db, monkeypatch, lost_step
):
    """Named View references survive rename, worker loss and durable reload."""
    from app.agentive.services import staging_command_batch
    from app.models.nodes import Entry

    original = staging_command_batch.apply_staged_command_once
    calls = 0

    async def interrupt(**kwargs):
        nonlocal calls
        result = await original(**kwargs)
        calls += 1
        if calls == lost_step:
            raise RuntimeError("Lost mixed-view worker")
        return result

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, track, single = await view_proposal(update=False)
        reset = current_scope_workspace_id.set(workspace.id)
        try:
            sc = await create_staged_change(
                user_id=auth.id,
                session_id=None,
                kind="batch",
                summary="Save and rename a view, then create a record",
                diff_human="Three approved changes",
                diff_machine={},
                payload={
                    "operations": [
                        {"kind": "save_view", "payload": single.payload},
                        {
                            "kind": "save_view",
                            "payload": {
                                **single.payload,
                                "view_id": "{{view.id:Approved view}}",
                                "name": "Revised view",
                            },
                        },
                        {
                            "kind": "create_entry",
                            "payload": {"track_id": track.id, "title": "One record"},
                        },
                    ]
                },
            )
        finally:
            current_scope_workspace_id.reset(reset)
        monkeypatch.setattr(
            staging_command_batch, "apply_staged_command_once", interrupt
        )
        with pytest.raises(RuntimeError, match="Lost mixed-view worker"):
            await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        staging._tokens.clear()
        monkeypatch.setattr(
            staging_command_batch, "apply_staged_command_once", original
        )
        result = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert result["consumed"] is True, result
        assert result["execute_result"]["completed"] == 3
        assert len(await View.find({"name": "Revised view"})) == 1
        assert await View.find({"name": "Approved view"}) == []
        assert len(await Entry.find({"track_id": track.id})) == 1
