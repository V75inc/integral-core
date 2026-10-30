"""Deletion commits its result and cleanup intent without replaying effects."""

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.agentive import staging
from app.agentive.services import staging_apply
from app.agentive.staging import StagingError, create_staged_change
from app.models.nodes import Entry
from app.services.agent_scope import current_scope_workspace_id
from tests.contract.test_staging_command_apply_postgres import proposal

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


async def deletion():
    auth, workspace, track, create = await proposal()
    made = await staging_apply.bless_and_execute(user_id=auth.id, token=create.token)
    entry_id = made["execute_result"]["entry"]["id"]
    entry = await Entry.find_one({"id": entry_id})
    from app.contracts.information import schema_revision_from_profile_version
    from app.services.operational_model_runtime import resolve_track_runtime_profile

    model, _, _ = await resolve_track_runtime_profile(track)
    schema_revision = schema_revision_from_profile_version(model.version_number)
    reset = current_scope_workspace_id.set(workspace.id)
    try:
        sc = await create_staged_change(
            user_id=auth.id,
            session_id=None,
            kind="delete_entry",
            summary="Delete",
            diff_human="Delete",
            diff_machine={},
            payload={
                "entry_id": entry_id,
                "expected_record_revision": entry.record_revision,
                "expected_schema_revision": schema_revision,
            },
        )
    finally:
        current_scope_workspace_id.reset(reset)
    return auth, track, entry, sc


async def test_delete_completion_recovery_keeps_receipt_and_cleanup_fact(
    postgres_raw_db, monkeypatch
):
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "77" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, entry, sc = await deletion()
        original = staging_apply.consume_token

        async def fail(**kwargs):
            raise StagingError("completion_unavailable", "Lost completion")

        monkeypatch.setattr(staging_apply, "consume_token", fail)
        first = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert first["application_state"] == "reconciliation_required", first
        assert await postgres_raw_db.get("node", entry.id) is None
        cleanup = await postgres_raw_db.find(
            "object", {"entity": "EntryCleanupFact", "context.entry_id": entry.id}
        )
        assert len(cleanup) == 1
        staging._tokens.clear()
        monkeypatch.setattr(staging_apply, "consume_token", original)
        recovered = await staging_apply.bless_and_execute(
            user_id=auth.id, token=sc.token
        )
        assert recovered["consumed"] is True, recovered
        assert recovered["execute_result"]["deleted"] is True
        assert recovered["execute_result"]["execution_receipt"]["replayed"] is True
        assert (
            await postgres_raw_db.find(
                "object", {"entity": "EntryCleanupFact", "context.entry_id": entry.id}
            )
            == cleanup
        )


async def test_delete_receipt_fault_rolls_back_record_cleanup_and_event(
    postgres_raw_db, monkeypatch
):
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "77" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, entry, sc = await deletion()
        before = await postgres_raw_db.find(
            "object", {"entity": "OperationEventOutbox"}
        )

        async def fail(**kwargs):
            raise OSError("Deletion receipt failure")

        monkeypatch.setattr(
            "app.agentive.services.staging_execution.record_staging_execution_result",
            fail,
        )
        result = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert result["consumed"] is False
        assert result["execute_result"]["error"] is True
        assert await postgres_raw_db.get("node", entry.id) is not None
        assert (
            await postgres_raw_db.find(
                "object", {"entity": "EntryCleanupFact", "context.entry_id": entry.id}
            )
            == []
        )
        assert (
            await postgres_raw_db.find("object", {"entity": "OperationEventOutbox"})
            == before
        )


async def test_delete_rejects_changed_record_since_approval_review(
    postgres_raw_db, monkeypatch
):
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "77" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, entry, sc = await deletion()
        entry.record_revision += 1
        await entry.save()
        result = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert result["consumed"] is False
        assert result["execute_result"]["status_code"] == 409
        assert await postgres_raw_db.get("node", entry.id) is not None


async def test_completed_create_then_delete_batch_recovers_private_references(
    postgres_raw_db, monkeypatch
):
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "77" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, track, _ = await proposal()
        reset = current_scope_workspace_id.set(workspace.id)
        try:
            sc = await create_staged_change(
                user_id=auth.id,
                session_id=None,
                kind="batch",
                summary="Create then delete",
                diff_human="Two steps",
                diff_machine={},
                payload={
                    "operations": [
                        {
                            "kind": "create_entry",
                            "payload": {"track_id": track.id, "title": "Temporary"},
                        },
                        {
                            "kind": "delete_entry",
                            "payload": {"entry_id": "{{entry.id:Temporary}}"},
                        },
                    ]
                },
            )
        finally:
            current_scope_workspace_id.reset(reset)
        original = staging_apply.consume_token

        async def fail(**kwargs):
            raise StagingError("completion_unavailable", "Lost completion")

        monkeypatch.setattr(staging_apply, "consume_token", fail)
        first = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert first["application_state"] == "reconciliation_required", first
        assert await Entry.find({"track_id": track.id}) == []
        deleted_id = first["execute_result"]["results"][0]["result"]["entry"]["id"]
        staging._tokens.clear()
        monkeypatch.setattr(staging_apply, "consume_token", original)
        recovered = await staging_apply.bless_and_execute(
            user_id=auth.id, token=sc.token
        )
        assert recovered["consumed"] is True, recovered
        assert recovered["execute_result"]["completed"] == 2
        assert recovered["execute_result"]["results"][1]["result"]["deleted"] is True
        assert deleted_id not in str(recovered["execute_result"])
        assert await Entry.find({"track_id": track.id}) == []


async def test_delete_rejects_changed_effective_schema(postgres_raw_db, monkeypatch):
    from app.contracts.information import schema_revision_from_profile_version
    from app.services.operational_model_runtime import resolve_track_runtime_profile

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "77" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, track, entry, sc = await deletion()
        model, _, _ = await resolve_track_runtime_profile(track)
        observed_schema = schema_revision_from_profile_version(model.version_number)
        model.version_number = observed_schema + 1
        await model.save()
        result = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert result["consumed"] is False
        assert result["execute_result"]["status_code"] == 409
        assert await postgres_raw_db.get("node", entry.id) is not None


async def track_deletion():
    auth, _, entry, _ = await deletion()
    from app.models.nodes import Track

    track = await Track.find_one({"id": entry.track_id})
    reset = current_scope_workspace_id.set(track.workspace_id)
    try:
        sc = await create_staged_change(
            user_id=auth.id,
            session_id=None,
            kind="delete_track",
            summary="Delete Track",
            diff_human="Delete Track and contained records",
            diff_machine={},
            payload={"track_id": track.id},
        )
    finally:
        current_scope_workspace_id.reset(reset)
    return auth, track, entry, sc


async def test_track_cascade_recovery_keeps_deleted_entry_cleanup(
    postgres_raw_db, monkeypatch
):
    from app.models.nodes import Track

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "77" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, track, entry, sc = await track_deletion()
        original = staging_apply.consume_token

        async def fail(**kwargs):
            raise StagingError("completion_unavailable", "Lost completion")

        monkeypatch.setattr(staging_apply, "consume_token", fail)
        first = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert first["application_state"] == "reconciliation_required", first
        assert await postgres_raw_db.get("node", track.id) is None
        assert await postgres_raw_db.get("node", entry.id) is None
        cleanup = await postgres_raw_db.find(
            "object", {"entity": "EntryCleanupFact", "context.entry_id": entry.id}
        )
        assert len(cleanup) == 1
        staging._tokens.clear()
        monkeypatch.setattr(staging_apply, "consume_token", original)
        recovered = await staging_apply.bless_and_execute(
            user_id=auth.id, token=sc.token
        )
        assert recovered["consumed"] is True
        assert recovered["execute_result"]["deleted"] is True
        assert track.id not in str(recovered["execute_result"])
        assert await Track.find({"id": track.id}) == []
        assert (
            await postgres_raw_db.find(
                "object", {"entity": "EntryCleanupFact", "context.entry_id": entry.id}
            )
            == cleanup
        )


async def test_track_cascade_receipt_fault_rolls_back_subtree(
    postgres_raw_db, monkeypatch
):
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "77" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, track, entry, sc = await track_deletion()

        async def fail(**kwargs):
            raise OSError("Track receipt failure")

        monkeypatch.setattr(
            "app.agentive.services.staging_execution.record_staging_execution_result",
            fail,
        )
        result = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert result["consumed"] is False
        assert result["execute_result"]["error"] is True
        assert await postgres_raw_db.get("node", track.id) is not None
        assert await postgres_raw_db.get("node", entry.id) is not None
        assert (
            await postgres_raw_db.find(
                "object", {"entity": "EntryCleanupFact", "context.entry_id": entry.id}
            )
            == []
        )


@pytest.mark.parametrize("fault", [False, True])
@pytest.mark.parametrize("lease_elapsed", [False, True])
async def test_app_hard_delete_retains_bundle_identity_with_graph_commit(
    postgres_raw_db, monkeypatch, fault, lease_elapsed
):
    from datetime import timedelta

    from app.agentive.services import staging_execution
    from app.agentive.services.staging_command_apply import apply_staged_command_once
    from app.agentive.staging_executors import dispatch_under_scope
    from app.models.nodes import App
    from app.services import app_deletion
    from app.services.comment_creation import create_comment_record

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "77" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, track, create = await proposal()
        made = await staging_apply.bless_and_execute(
            user_id=auth.id, token=create.token
        )
        entry_id = made["execute_result"]["entry"]["id"]
        root = await create_comment_record(
            user_id=auth.id, entry_id=entry_id, text="Discussion"
        )
        reply = await create_comment_record(
            user_id=auth.id, entry_id=entry_id, text="Reply", parent_id=root.comment.id
        )
        now = staging_execution._now()
        monkeypatch.setattr(staging_execution, "_now", lambda: now)
        if lease_elapsed:
            original = app_deletion._delete_app_admitted

            async def finish_after_lease(*args, **kwargs):
                nonlocal now
                result = await original(*args, **kwargs)
                now += timedelta(seconds=120)
                return result

            monkeypatch.setattr(
                app_deletion, "_delete_app_admitted", finish_after_lease
            )
        app = (await App.find({"workspace_id": workspace.id}))[0]
        reset = current_scope_workspace_id.set(workspace.id)
        try:
            sc = await create_staged_change(
                user_id=auth.id,
                session_id=None,
                kind="delete_app",
                summary="Delete App",
                diff_human="Delete App",
                diff_machine={},
                payload={"app_id": app.id},
            )
        finally:
            current_scope_workspace_id.reset(reset)
        await staging.bless_token(user_id=auth.id, token=sc.token)
        if fault:

            async def fail(**kwargs):
                raise OSError("App receipt failure")

            monkeypatch.setattr(
                "app.agentive.services.staging_execution.record_staging_execution_result",
                fail,
            )

        async def execute():
            return await dispatch_under_scope(
                user_id=auth.id,
                kind=sc.kind,
                payload=sc.payload,
                workspace_id=workspace.id,
            )

        result = await apply_staged_command_once(sc=sc, request=None, execute=execute)
        facts = await postgres_raw_db.find(
            "object", {"entity": "BundleCleanupFact", "context.app_id": app.id}
        )
        if fault:
            assert result["error"] is True
            assert await postgres_raw_db.get("node", app.id) is not None
            assert facts == []
        else:
            assert result["execution_receipt"]["status"] == "applied"
            assert await postgres_raw_db.get("node", app.id) is None
            assert len(facts) == 1
            assert facts[0]["context"]["workspace_id"] == workspace.id
        for identity in (track.id, entry_id, root.comment.id, reply.comment.id):
            row = await postgres_raw_db.get("node", identity)
            assert (row is not None) is fault
