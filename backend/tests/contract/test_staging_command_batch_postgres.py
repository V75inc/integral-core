"""Batch recovery uses step receipts, never a lagging cursor or old response."""

from dataclasses import replace

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.agentive import staging
from app.agentive.services import staging_apply, staging_command_batch
from app.agentive.staging import StagingError, create_staged_change
from app.models.nodes import Comment, Entry, Workspace
from app.services.agent_scope import current_scope_workspace_id
from tests.contract.test_staging_command_apply_postgres import proposal

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


async def batch():
    """Build a scoped Entry creation and update proposal."""
    auth, workspace, track, _ = await proposal()
    reset = current_scope_workspace_id.set(workspace.id)
    try:
        sc = await create_staged_change(
            user_id=auth.id,
            session_id=None,
            kind="batch",
            summary="Create and rename",
            diff_human="Three steps",
            diff_machine={},
            payload={
                "operations": [
                    {
                        "kind": "create_entry",
                        "payload": {"track_id": track.id, "title": "First"},
                    },
                    {
                        "kind": "create_entry",
                        "payload": {"track_id": track.id, "title": "Second"},
                    },
                    {
                        "kind": "update_entry",
                        "payload": {
                            "entry_id": "{{entry.id:First}}",
                            "title": "Renamed",
                        },
                    },
                ]
            },
        )
    finally:
        current_scope_workspace_id.reset(reset)
    return auth, track, sc


async def comment_batch():
    """Exercise references to an Entry and a subsequently removed Comment."""
    auth, workspace, track, _ = await proposal()
    reset = current_scope_workspace_id.set(workspace.id)
    try:
        sc = await create_staged_change(
            user_id=auth.id,
            session_id=None,
            kind="batch",
            summary="Create, discuss and revise a record",
            diff_human="Six approved record and comment changes",
            diff_machine={},
            payload={
                "operations": [
                    {
                        "kind": "create_entry",
                        "payload": {"track_id": track.id, "title": "Original"},
                    },
                    {
                        "kind": "add_comment",
                        "payload": {"entry_id": "{{entry.id}}", "body": "First"},
                    },
                    {
                        "kind": "edit_comment",
                        "payload": {"comment_id": "{{comment.id}}", "text": "Edited"},
                    },
                    {
                        "kind": "update_entry",
                        "payload": {"entry_id": "{{entry.id}}", "title": "Revised"},
                    },
                    {
                        "kind": "delete_comment",
                        "payload": {"comment_id": "{{comment.id}}"},
                    },
                    {
                        "kind": "add_comment",
                        "payload": {"entry_id": "{{entry.id}}", "body": "Final"},
                    },
                ]
            },
        )
    finally:
        current_scope_workspace_id.reset(reset)
    return auth, track, sc


@pytest.mark.parametrize("interrupted_step", range(1, 7))
async def test_mixed_comment_batch_recovers_every_committed_step(
    postgres_raw_db, monkeypatch, interrupted_step
):
    """Loss before progress persistence cannot duplicate or undo any child."""
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "33" * 32)
    original = staging_command_batch.apply_staged_command_once
    calls = 0

    async def interrupt(**kwargs):
        nonlocal calls
        result = await original(**kwargs)
        calls += 1
        if calls == interrupted_step:
            raise RuntimeError("Lost worker after committed batch step")
        return result

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, track, sc = await comment_batch()
        monkeypatch.setattr(
            staging_command_batch, "apply_staged_command_once", interrupt
        )
        with pytest.raises(RuntimeError, match="Lost worker"):
            await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        entries = await Entry.find({"track_id": track.id})
        assert len(entries) == 1
        entry_id = entries[0].id
        revision = entries[0].record_revision
        staging._tokens.clear()
        monkeypatch.setattr(
            staging_command_batch, "apply_staged_command_once", original
        )
        recovered = await staging_apply.bless_and_execute(
            user_id=auth.id, token=sc.token
        )
        assert recovered["consumed"] is True, recovered
        assert recovered["execute_result"]["completed"] == 6
        entries = await Entry.find({"track_id": track.id})
        assert len(entries) == 1 and entries[0].id == entry_id
        assert entries[0].title == "Revised"
        assert entries[0].record_revision == revision + (interrupted_step < 4)
        comments = await Comment.find()
        assert len(comments) == 1 and comments[0].text == "Final"


async def test_completed_mixed_batch_reconciles_deleted_comment_references(
    postgres_raw_db, monkeypatch
):
    """A lost parent completion must not resurrect its deleted child Comment."""
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "33" * 32)
    consume = staging_apply.consume_token

    async def unavailable(**kwargs):
        raise StagingError("completion_unavailable", "Lost parent completion")

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, track, sc = await comment_batch()
        monkeypatch.setattr(staging_apply, "consume_token", unavailable)
        first = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert first["application_state"] == "reconciliation_required", first
        entries = await Entry.find({"track_id": track.id})
        comments = await Comment.find()
        before = [(entry.id, entry.record_revision) for entry in entries]
        assert len(before) == 1
        assert len(comments) == 1 and comments[0].text == "Final"
        comment_id = comments[0].id
        staging._tokens.clear()
        monkeypatch.setattr(staging_apply, "consume_token", consume)
        recovered = await staging_apply.bless_and_execute(
            user_id=auth.id, token=sc.token
        )
        assert recovered["consumed"] is True, recovered
        assert recovered["execute_result"]["completed"] == 6
        assert [
            (entry.id, entry.record_revision)
            for entry in await Entry.find({"track_id": track.id})
        ] == before
        comments = await Comment.find()
        assert len(comments) == 1 and comments[0].id == comment_id
        assert comments[0].text == "Final"


async def test_mixed_batch_recovery_cannot_continue_after_membership_revocation(
    postgres_raw_db, monkeypatch
):
    """Saved receipts preserve prior effects but never replace live authority."""
    from tests.contract.test_release_admission_postgres import remove_membership

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "33" * 32)
    original = staging_command_batch.apply_staged_command_once
    calls = 0

    async def interrupt(**kwargs):
        nonlocal calls
        result = await original(**kwargs)
        calls += 1
        if calls == 2:
            raise RuntimeError("Lost worker after comment creation")
        return result

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, track, sc = await comment_batch()
        monkeypatch.setattr(
            staging_command_batch, "apply_staged_command_once", interrupt
        )
        with pytest.raises(RuntimeError, match="Lost worker"):
            await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        before = (await Entry.find({"track_id": track.id}))[0]
        comment = (await Comment.find())[0]
        await remove_membership(auth, await Workspace.get(sc.workspace_id))
        staging._tokens.clear()
        monkeypatch.setattr(
            staging_command_batch, "apply_staged_command_once", original
        )
        result = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert result["consumed"] is False, result
        assert result["execute_result"]["error"] is True, result
        entries = await Entry.find({"track_id": track.id})
        comments = await Comment.find()
        assert len(entries) == 1 and entries[0].id == before.id
        assert entries[0].title == "Original"
        assert entries[0].record_revision == before.record_revision
        assert len(comments) == 1 and comments[0].id == comment.id
        assert comments[0].text == "First"
        assert current_scope_workspace_id.get() is None


async def test_complete_batch_recovery_keeps_ids_named_refs_and_revisions(
    postgres_raw_db, monkeypatch
):
    """Parent reconciliation preserves each committed Entry revision."""
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "33" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, track, sc = await batch()
        original = staging_apply.consume_token

        async def fail(**kwargs):
            raise StagingError("completion_unavailable", "Injected completion failure")

        monkeypatch.setattr(staging_apply, "consume_token", fail)
        first = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert first.get("application_state") == "reconciliation_required", first
        before = {
            entry.id: entry.record_revision
            for entry in await Entry.find({"track_id": track.id})
        }
        assert len(before) == 2
        staging._tokens.clear()
        monkeypatch.setattr(staging_apply, "consume_token", original)
        second = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert second["consumed"] is True, second
        assert {
            entry.id: entry.record_revision
            for entry in await Entry.find({"track_id": track.id})
        } == before


async def test_worker_loss_after_step_commit_never_recreates_first_entry(
    postgres_raw_db, monkeypatch
):
    """A missing progress cursor cannot authorize another creation."""
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "33" * 32)
    original = staging_command_batch.apply_staged_command_once
    calls = 0

    async def interrupt(**kwargs):
        nonlocal calls
        result = await original(**kwargs)
        calls += 1
        if calls == 1:
            raise RuntimeError("Lost worker before updating progress")
        return result

    monkeypatch.setattr(staging_command_batch, "apply_staged_command_once", interrupt)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, track, sc = await batch()
        with pytest.raises(RuntimeError):
            await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        first_id = (await Entry.find({"track_id": track.id}))[0].id
        assert sc.progress is None
        staging._tokens.clear()
        monkeypatch.setattr(
            staging_command_batch, "apply_staged_command_once", original
        )
        recovered = await staging_apply.bless_and_execute(
            user_id=auth.id, token=sc.token
        )
        assert recovered["consumed"] is True, recovered
        entries = await Entry.find({"track_id": track.id})
        assert len(entries) == 2
        assert first_id in {entry.id for entry in entries}
        assert (await Entry.get(first_id)).title == "Renamed"


async def test_legacy_approved_batch_without_journal_cannot_silently_replay(
    postgres_raw_db, monkeypatch
):
    """An older approval needs reconciliation before executing any child."""
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "33" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, track, sc = await batch()
        await staging.bless_token(user_id=auth.id, token=sc.token)
        legacy = replace(sc, execution_contract_version="")
        with pytest.raises(StagingError) as exc:
            await staging_command_batch.apply_command_batch_once(
                sc=legacy, request=None
            )
        assert exc.value.code == "legacy_execution_reconciliation_required"
        assert await Entry.find({"track_id": track.id}) == []


async def test_expired_partial_batch_retains_committed_step(
    postgres_raw_db, monkeypatch
):
    """Expiry preserves applied evidence without authorizing unfinished work."""
    from app.agentive import staging_store
    from tests.contract.test_staging_decisions_postgres import age_proposal

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "33" * 32)
    original = staging_command_batch.apply_staged_command_once

    async def stop_after_commit(**kwargs):
        await original(**kwargs)
        raise RuntimeError("Worker lost after child graph commit")

    monkeypatch.setattr(
        staging_command_batch, "apply_staged_command_once", stop_after_commit
    )
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, track, sc = await batch()
        with pytest.raises(RuntimeError):
            await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        await age_proposal(postgres_raw_db, sc)
        staging._tokens.clear()
        observed = await staging.get_token(sc.token)
        assert observed.state == "blessed"
        assert observed.last_error["application_state"] == "reconciliation_required"
        assert observed.last_error["partially_applied"] is True
        assert observed.last_error["applied"] is False
        assert await staging_store.load_current(sc.token) is not None
        assert await staging_store.load_decision_state(sc.token, auth.id) is None
        with pytest.raises(StagingError) as exc:
            await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert exc.value.code == "expired"
        assert len(await Entry.find({"track_id": track.id})) == 1


async def test_completed_batch_can_reconcile_after_deadline(
    postgres_raw_db, monkeypatch
):
    """Completion after expiry reads receipts rather than applying new effects."""
    from tests.contract.test_staging_decisions_postgres import age_proposal

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "33" * 32)
    original = staging_apply.consume_token

    async def fail_completion(**kwargs):
        raise StagingError("completion_unavailable", "Worker lost before completion")

    monkeypatch.setattr(staging_apply, "consume_token", fail_completion)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, track, sc = await batch()
        first = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert first["application_state"] == "reconciliation_required"
        await age_proposal(postgres_raw_db, sc)
        staging._tokens.clear()
        monkeypatch.setattr(staging_apply, "consume_token", original)
        recovered = await staging_apply.bless_and_execute(
            user_id=auth.id, token=sc.token
        )
        assert recovered["consumed"] is True
        assert len(await Entry.find({"track_id": track.id})) == 2
