"""Approved model edits recover committed graph changes without replay."""

import asyncio
from contextvars import Context

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async
from jvspatial.db.factory import create_database

from app.agentive import staging
from app.agentive.services import staging_apply, staging_execution
from app.agentive.staging import StagingError, create_staged_change
from app.models.nodes import App
from app.services.agent_scope import current_scope_workspace_id
from app.services.app_graph import (
    ensure_app_attached_operational_model,
    ensure_track_attached_operational_model,
)
from app.services.operational_model_authoring import modify_operational_model
from app.services.resource_location import locate_resource
from tests.contract.test_staging_command_apply_postgres import proposal

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]
ACTIONS = [
    "add_entry_type",
    "remove_entry_type",
    "add_tag",
    "remove_tag",
    "add_view",
    "update_view",
    "remove_view",
]
CASES = [
    (scope, action)
    for scope in ("track", "app")
    for action in ACTIONS
    if not (scope == "app" and action == "update_view")
]


@pytest.fixture(autouse=True)
def receipt_key(monkeypatch):
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "77" * 32)


async def model_proposal(scope, action):
    auth, workspace, track, _ = await proposal()
    if scope == "track":
        model = await ensure_track_attached_operational_model(track)
        target = {"track_id": track.id}
    else:
        location = await locate_resource("track", track.id)
        app = await App.get(location.app_id)
        model = await ensure_app_attached_operational_model(app)
        target = {"app_id": app.id}
    payload = {**target, "action": action, "name": "Approved model child"}
    reset = current_scope_workspace_id.set(workspace.id)
    try:
        if action.startswith("remove_") or action == "update_view":
            kind = action.removeprefix("remove_") if action != "update_view" else "view"
            result = await modify_operational_model(
                user_id=auth.id, **target, action=f"add_{kind}", name="Original child"
            )
            assert not result.get("error"), result
            payload[f"{kind}_id"] = result[f"{kind}_id"]
        sc = await create_staged_change(
            user_id=auth.id,
            session_id=None,
            kind=f"modify_operational_model.{action}",
            summary="Edit model",
            diff_human="Apply approved model edit",
            diff_machine={},
            payload=payload,
        )
    finally:
        current_scope_workspace_id.reset(reset)
    return auth, workspace, model, sc


@pytest.mark.parametrize("scope,action", CASES)
async def test_model_edit_completion_loss_recovers_without_replay(
    postgres_raw_db, monkeypatch, scope, action
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, model, sc = await model_proposal(scope, action)
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
        nodes = await postgres_raw_db.find("node", {}, sort=[("id", 1)])
        edges = await postgres_raw_db.find("edge", {}, sort=[("id", 1)])
        staging._tokens.clear()
        monkeypatch.setattr(staging_apply, "consume_token", consume)
        result = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert result["consumed"] is True, result
        assert result["execute_result"]["execution_receipt"]["replayed"] is True
        assert result["execute_result"]["operational_model"]["id"] == model.id
        assert await postgres_raw_db.find("node", {}, sort=[("id", 1)]) == nodes
        assert await postgres_raw_db.find("edge", {}, sort=[("id", 1)]) == edges


@pytest.mark.parametrize("scope,action", CASES)
async def test_model_receipt_failure_rolls_back_all_graph_and_event_changes(
    postgres_raw_db, monkeypatch, scope, action
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, _, sc = await model_proposal(scope, action)
        nodes = await postgres_raw_db.find("node", {}, sort=[("id", 1)])
        edges = await postgres_raw_db.find("edge", {}, sort=[("id", 1)])
        events = await postgres_raw_db.find(
            "object", {"entity": "OperationEventOutbox"}, sort=[("id", 1)]
        )

        async def unavailable(**kwargs):
            raise RuntimeError("Injected receipt failure")

        monkeypatch.setattr(
            staging_execution, "record_staging_execution_result", unavailable
        )
        result = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert result["consumed"] is False, result
        assert result["execute_result"]["error"], result
        assert await postgres_raw_db.find("node", {}, sort=[("id", 1)]) == nodes
        assert await postgres_raw_db.find("edge", {}, sort=[("id", 1)]) == edges
        assert (
            await postgres_raw_db.find(
                "object", {"entity": "OperationEventOutbox"}, sort=[("id", 1)]
            )
            == events
        )


@pytest.mark.parametrize("scope", ["track", "app"])
async def test_selected_owner_cannot_redirect_edit_through_scalar_model_cache(
    postgres_raw_db, scope
):
    from app.models.edges import CONTAINS, OWNS
    from app.models.nodes import Track
    from app.services.app_graph import catalog_app, catalog_track
    from app.services.permissions import get_user_node

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, own_model, sc = await model_proposal(scope, "add_tag")
        owner = await get_user_node(auth.id)
        target_id = sc.payload[f"{scope}_id"]
        target = await (
            Track.get(target_id) if scope == "track" else App.get(target_id)
        )
        if scope == "track":
            other = await Track.create(title="Other track", workspace_id=workspace.id)
            await catalog_track(other)
            location = await locate_resource("track", target.id)
            app = await App.get(location.app_id)
            await app.connect(other, edge=CONTAINS)
            await owner.connect(other, edge=OWNS)
            other_model = await ensure_track_attached_operational_model(other)
        else:
            other = await App.create(name="Other app", workspace_id=workspace.id)
            await catalog_app(other)
            await owner.connect(other, edge=OWNS)
            other_model = await ensure_app_attached_operational_model(other)
        target.attached_operational_model_id = other_model.id
        await target.save()
        before = await postgres_raw_db.find("node", {}, sort=[("id", 1)])
        reset = current_scope_workspace_id.set(workspace.id)
        try:
            result = await modify_operational_model(
                user_id=auth.id,
                action="add_tag",
                name="Must not be created",
                **{f"{scope}_id": target.id},
            )
        finally:
            current_scope_workspace_id.reset(reset)
        assert result.get("error") == "not_found", result
        assert await postgres_raw_db.find("node", {}, sort=[("id", 1)]) == before


@pytest.mark.parametrize("scope", ["track", "app"])
async def test_model_recovery_withholds_after_workspace_membership_removal(
    postgres_raw_db, monkeypatch, scope
):
    from app.models.edges import IS_MEMBER_OF
    from app.services.permissions import get_user_node

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, model, sc = await model_proposal(scope, "add_tag")
        consume = staging_apply.consume_token

        async def unavailable(**kwargs):
            raise StagingError("completion_unavailable", "Injected completion loss")

        monkeypatch.setattr(staging_apply, "consume_token", unavailable)
        first = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert first.get("application_state") == "reconciliation_required", first
        user = await get_user_node(auth.id)
        memberships = await postgres_raw_db.find(
            "edge",
            {
                "source": user.id,
                "target": workspace.id,
                "entity": IS_MEMBER_OF._entity_name(),
            },
        )
        assert len(memberships) == 1
        await postgres_raw_db.delete("edge", memberships[0]["id"])
        staging._tokens.clear()
        monkeypatch.setattr(staging_apply, "consume_token", consume)
        recovered = await staging_apply.bless_and_execute(
            user_id=auth.id, token=sc.token
        )
        result = recovered["execute_result"]
        assert recovered["consumed"] is True, recovered
        assert result["result_access"] == "withheld", result
        assert model.id not in str(result)
        assert "Approved model child" not in str(result)


@pytest.mark.parametrize("scope", ["track", "app"])
async def test_model_edit_rejects_event_for_an_unselected_model(
    postgres_raw_db, monkeypatch, scope
):
    from app.services import change_event

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, _, sc = await model_proposal(scope, "add_tag")
        before = await postgres_raw_db.find("node", {}, sort=[("id", 1)])
        emit = change_event.emit_change_event

        async def redirected(**kwargs):
            kwargs["scope"] = "operational_model:n.OperationalModel.unselected"
            return await emit(**kwargs)

        monkeypatch.setattr(change_event, "emit_change_event", redirected)
        result = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert result["consumed"] is False, result
        assert result["execute_result"]["error_code"] == "event_scope_invalid", result
        assert await postgres_raw_db.find("node", {}, sort=[("id", 1)]) == before


@pytest.mark.parametrize("interrupted_step", range(1, 7))
async def test_model_child_batch_recovers_named_references_after_deletion(
    postgres_raw_db, monkeypatch, interrupted_step
):
    from app.agentive.services import staging_command_batch

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, _, template = await model_proposal("track", "add_tag")
        operations = []
        for child in ("tag", "entry_type", "view"):
            operations.extend(
                [
                    {
                        "kind": f"modify_operational_model.add_{child}",
                        "payload": {
                            **template.payload,
                            "action": f"add_{child}",
                            "name": f"Batch {child}",
                        },
                    },
                    {
                        "kind": f"modify_operational_model.remove_{child}",
                        "payload": {
                            "track_id": template.payload["track_id"],
                            "action": f"remove_{child}",
                            f"{child}_id": "{{" + child + ".id:Batch " + child + "}}",
                        },
                    },
                ]
            )
        reset = current_scope_workspace_id.set(workspace.id)
        try:
            sc = await create_staged_change(
                user_id=auth.id,
                session_id=None,
                kind="batch",
                summary="Edit model children",
                diff_human="Add and remove model children",
                diff_machine={},
                payload={"operations": operations},
            )
        finally:
            current_scope_workspace_id.reset(reset)
        apply = staging_command_batch.apply_staged_command_once
        calls = 0

        async def lost_worker(**kwargs):
            nonlocal calls
            result = await apply(**kwargs)
            assert not result.get("error"), result
            calls += 1
            if calls == interrupted_step:
                raise RuntimeError("Lost model batch worker")
            return result

        monkeypatch.setattr(
            staging_command_batch, "apply_staged_command_once", lost_worker
        )
        with pytest.raises(RuntimeError, match="Lost model batch worker"):
            await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        staging._tokens.clear()
        monkeypatch.setattr(staging_command_batch, "apply_staged_command_once", apply)
        recovered = await staging_apply.bless_and_execute(
            user_id=auth.id, token=sc.token
        )
        assert recovered["consumed"] is True, recovered
        assert recovered["execute_result"]["completed"] == 6, recovered
        for index in range(6):
            receipt = await postgres_raw_db.get(
                "object",
                staging_execution.execution_record_id(f"{sc.token}:step:{index}"),
            )
            assert receipt["context"]["state"] == "applied"
        for entity, child_kind in (
            ("Tag", "tag"),
            ("EntryType", "entry_type"),
            ("View", "view"),
        ):
            assert not await postgres_raw_db.find(
                "node", {"entity": entity, "context.name": f"Batch {child_kind}"}
            )


@pytest.mark.parametrize("scope", ["track", "app"])
async def test_model_event_delivery_observes_committed_graph_and_receipt(
    postgres_raw_db, monkeypatch, scope
):
    from app.services import workspace_membership
    from app.services.app_operations.event_outbox import event_outbox_object_id
    from app.services.app_operations.transaction_scope import (
        OperationTransactionUnavailable,
        current_graph_transaction_database,
    )

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, _, sc = await model_proposal(scope, "add_tag")
        delivered = []

        async def deliver(outbox):
            with pytest.raises(OperationTransactionUnavailable):
                current_graph_transaction_database()
            row = await postgres_raw_db.get("object", event_outbox_object_id(outbox))
            assert row is not None
            receipt = await postgres_raw_db.get(
                "object", staging_execution.execution_record_id(sc.token)
            )
            assert receipt["context"]["state"] == "applied"
            delivered.append(outbox)

        monkeypatch.setattr(workspace_membership, "_deliver", deliver)
        result = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert result["consumed"] is True, result
        assert len(delivered) == 1


@pytest.mark.parametrize("scope", ["track", "app"])
async def test_model_recovery_propagates_current_attachment_outage(
    postgres_raw_db, monkeypatch, scope
):
    from app.agentive.services.staging_model_commands import readback
    from app.exceptions import AuthorizationUnavailableError
    from app.services import attached_model_scope

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, model, sc = await model_proposal(scope, "add_tag")
        result = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert result["consumed"] is True, result

        async def unavailable(*args, **kwargs):
            raise AuthorizationUnavailableError()

        monkeypatch.setattr(attached_model_scope, "read_attached_model", unavailable)
        with pytest.raises(AuthorizationUnavailableError):
            await readback(
                sc,
                workspace.id,
                {
                    "owner_kind": scope,
                    "owner_id": sc.payload[f"{scope}_id"],
                    "model_id": model.id,
                },
            )
        assert current_scope_workspace_id.get() is None


@pytest.mark.parametrize("scope", ["track", "app"])
async def test_workspace_revocation_waits_for_admitted_model_edit(
    postgres_raw_db, monkeypatch, scope
):
    """Revocation cannot commit between model authority admission and its write."""
    from app.services import operational_model_runtime
    from app.services.membership_write_scope import membership_write_scope
    from tests.contract.test_release_admission_postgres import remove_membership

    second = create_database(db_type="postgres", min_size=1, max_size=1)
    writing = revoking = None
    admitted, finish = asyncio.Event(), asyncio.Event()
    synchronize = operational_model_runtime.sync_attached_manifest

    async def hold(*args, **kwargs):
        admitted.set()
        await finish.wait()
        return await synchronize(*args, **kwargs)

    try:
        async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
            auth, workspace, _, sc = await model_proposal(scope, "add_tag")
            # Prewarm authorization state/indexes so DDL cannot masquerade as
            # a resource authority lock in the competing transaction.
            async with membership_write_scope(workspace.id, principal_ids=(auth.id,)):
                pass
            for collection in ("node", "edge", "object"):
                await second.find(collection, {"id": ""}, limit=1)
            monkeypatch.setattr(
                operational_model_runtime, "sync_attached_manifest", hold
            )

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
