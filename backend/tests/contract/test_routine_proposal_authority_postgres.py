"""A schedule cannot infer approval authority from another card's timestamp."""

import uuid

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.agentive.services import routine_tasks, staging_apply
from app.agentive.staging import create_staged_change, get_token
from app.services.chat_threads import create_thread, update_provider_session
from app.services.routine_task_scheduler import _reconcile_write_scope
from app.utils.time import utc_now
from tests.contract.test_staging_command_apply_postgres import proposal

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


async def routine_and_entry():
    auth, workspace, _, change = await proposal()
    created = await staging_apply.bless_and_execute(user_id=auth.id, token=change.token)
    assert created["consumed"], created
    entry = created["execute_result"]["entry"]
    thread = await create_thread(
        user_id=auth.id, workspace_id=workspace.id, provider_id="integral_native"
    )
    await update_provider_session(thread, "routine-provenance-" + uuid.uuid4().hex)
    routine = await routine_tasks.create_routine_task(
        user_id=auth.id,
        workspace_id=workspace.id,
        thread_id=thread.id,
        instruction="Update the allowed record.",
        cron="0 9 * * *",
        write_scope=[{"resource_type": "entry", "resource_id": entry["id"]}],
    )
    return auth, workspace, entry, thread, routine


@pytest.mark.parametrize("condition", ["human_mid_turn", "cancelled", "grant_removed"])
async def test_unreviewed_card_is_not_routine_authority(
    postgres_raw_db, monkeypatch, condition
):
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "aa" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, entry, thread, routine = await routine_and_entry()
        stale = routine.model_copy()
        started = utc_now()
        user_card = await create_staged_change(
            user_id=auth.id,
            workspace_id=workspace.id,
            session_id=thread.provider_session_id,
            kind="update_entry",
            summary="Unreviewed human edit",
            diff_human="Unreviewed human edit",
            diff_machine={},
            payload={"entry_id": entry["id"], "title": "Not approved by the user"},
        )
        if condition == "cancelled":
            await routine_tasks.cancel_routine_task(
                user_id=auth.id, routine_id=routine.id
            )
        elif condition == "grant_removed":
            await routine_tasks.update_routine_task(
                user_id=auth.id, routine_id=routine.id, write_scope=[]
            )
        before = await postgres_raw_db.get("node", entry["id"])
        await _reconcile_write_scope(
            stale, preexisting_tokens=set(), turn_started_at=started
        )
        assert await postgres_raw_db.get("node", entry["id"]) == before
        assert (await get_token(user_card.token)).state == "pending"


async def claimed_with_grant():
    from app.agentive.services import work_execution, work_items

    auth, workspace, entry, thread, routine = await routine_and_entry()
    queued = await routine_tasks.enqueue_routine_turn_work(
        routine_id=routine.id,
        principal_id=auth.id,
        workspace_id=workspace.id,
        thread_id=thread.id,
        scheduled_for="2026-10-16T09:00:00+00:00",
    )
    claimed = await work_items.claim_due_candidate(
        worker_id="proposal-origin-proof",
        work_item_id=queued.work_item_id,
        lease_seconds=120,
    )
    assert claimed is not None
    ctx = work_execution.build_work_execution_context(
        work_item=claimed, logical_step_key="proposal:update:0"
    )
    return auth, workspace, entry, thread, routine, claimed, ctx


async def test_current_broker_proposal_applies_only_its_approved_scope(
    postgres_raw_db, monkeypatch
):
    from app.agentive.services.capability_broker import invoke_declared_capability
    from app.agentive.services.execution_runs import RunStep, start_run
    from app.agentive.services.staging_execution import execution_record_id
    from app.services.credential_crypto import decrypt_secret_from_storage

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "aa" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, entry, thread, _, claimed, ctx = await claimed_with_grant()
        await start_run(
            thread_id=thread.id,
            user_id=auth.id,
            workspace_id=workspace.id,
            provider_id="integral_native",
            origin="routine_task",
            run_id=ctx.run_id,
            work_item_id=claimed.work_item_id,
        )
        result = await invoke_declared_capability(
            principal_id=auth.id,
            workspace_id=workspace.id,
            source="core",
            capability_key="integral_update_entry",
            op_class="propose",
            origin="chat",
            run_id=ctx.run_id,
            work_execution_context=ctx,
            session_id=thread.id,
            idempotency_key="routine-proposal-allowed",
            arguments={
                "entry_id": entry["id"],
                "updates": {"title": "Routine applied"},
            },
        )
        assert result.ok, result
        assert result.data["state"] == "consumed", result
        row = await postgres_raw_db.get("node", entry["id"])
        assert row["context"]["title"] == "Routine applied"
        assert row["context"]["record_revision"] == 2
        assert result.receipt.status == "succeeded"
        token = result.data["token"]
        private = await postgres_raw_db.get("object", execution_record_id(token))
        assert private["context"]["state"] == "applied"
        import json

        proof = json.loads(
            decrypt_secret_from_storage(
                private["context"]["origin_ciphertext"],
                aad=execution_record_id(token) + ":proposal-origin",
            )
        )
        assert proof["work_item_id"] == claimed.work_item_id
        assert proof["run_id"] == ctx.run_id
        assert proof["capability_key"] == "integral_update_entry"
        assert "lease_token" not in proof
        assert "origin_proof" not in result.data
        steps = await RunStep.find({"run_id": ctx.run_id})
        assert len(steps) == 1 and steps[0].status == "succeeded"

        # A later owner change is current state. Reobserving the same broker
        # call returns that state without repeating the older approved delta.
        from app.services.entry_update import update_entry_in_track

        await update_entry_in_track(
            entry_id=entry["id"],
            user_id=auth.id,
            workspace_id=workspace.id,
            title="Later owner edit",
        )
        before = await postgres_raw_db.get("node", entry["id"])
        repeated = await invoke_declared_capability(
            principal_id=auth.id,
            workspace_id=workspace.id,
            source="core",
            capability_key="integral_update_entry",
            op_class="propose",
            origin="chat",
            run_id=ctx.run_id,
            work_execution_context=ctx,
            session_id=thread.id,
            idempotency_key="routine-proposal-allowed",
            arguments={
                "entry_id": entry["id"],
                "updates": {"title": "Routine applied"},
            },
        )
        assert repeated.ok and repeated.replayed, repeated
        assert repeated.data["state"] == "consumed"
        assert "summary" not in repeated.data
        assert "diff_human" not in repeated.data
        assert "diff_machine" not in repeated.data
        assert repeated.data["execute_result"]["entry"]["title"] == "Later owner edit"
        assert await postgres_raw_db.get("node", entry["id"]) == before
        changed = await invoke_declared_capability(
            principal_id=auth.id,
            workspace_id=workspace.id,
            source="core",
            capability_key="integral_update_entry",
            op_class="propose",
            origin="chat",
            run_id=ctx.run_id,
            work_execution_context=ctx,
            session_id=thread.id,
            idempotency_key="routine-proposal-allowed",
            arguments={"entry_id": entry["id"], "updates": {"title": "Changed input"}},
        )
        assert (
            not changed.ok and changed.error_code == "capability.idempotency_conflict"
        )
        assert await postgres_raw_db.get("node", entry["id"]) == before


@pytest.mark.parametrize(
    "condition", ["wrong_target_type", "removed_grant", "changed_instruction", "human"]
)
async def test_broker_requires_live_exact_routine_authority(
    postgres_raw_db, monkeypatch, condition
):
    from app.agentive.services.capability_broker import invoke_declared_capability
    from app.agentive.services.execution_runs import start_run

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "aa" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, entry, thread, routine, claimed, ctx = (
            await claimed_with_grant()
        )
        if condition == "wrong_target_type":
            # Save a fresh reviewed mandate with the same ID but wrong type.
            from app.agentive.services.routine_mandate import (
                routine_mandate_fingerprint,
            )

            routine.write_scope = [
                {"resource_type": "track", "resource_id": entry["id"]}
            ]
            await routine.save()
            claimed.input_payload["routine_mandate_fingerprint"] = (
                routine_mandate_fingerprint(routine)
            )
            await claimed.save()
        elif condition == "removed_grant":
            await routine_tasks.update_routine_task(
                user_id=auth.id, routine_id=routine.id, write_scope=[]
            )
        elif condition == "changed_instruction":
            await routine_tasks.update_routine_task(
                user_id=auth.id,
                routine_id=routine.id,
                instruction="An unreviewed replacement instruction.",
            )
        await start_run(
            thread_id=thread.id,
            user_id=auth.id,
            workspace_id=workspace.id,
            provider_id="integral_native",
            origin="routine_task" if condition != "human" else "chat",
            run_id=ctx.run_id,
            work_item_id=claimed.work_item_id if condition != "human" else "",
        )
        before = await postgres_raw_db.get("node", entry["id"])
        result = await invoke_declared_capability(
            principal_id=auth.id,
            workspace_id=workspace.id,
            source="core",
            capability_key="integral_update_entry",
            op_class="propose",
            origin="chat",
            run_id=ctx.run_id,
            work_execution_context=ctx if condition != "human" else None,
            session_id=thread.id,
            idempotency_key="routine-proposal-refused-" + condition,
            arguments={"entry_id": entry["id"], "updates": {"title": "Not authorized"}},
        )
        assert await postgres_raw_db.get("node", entry["id"]) == before
        if condition in {"removed_grant", "changed_instruction"}:
            assert not result.ok and result.error_code == "work.policy_denied", result
        else:
            assert result.ok and result.data["state"] == "pending", result
            assert result.receipt.status == "waiting_for_human"
            card = await get_token(result.data["token"])
            assert card is not None and card.state == "pending"
            if condition == "human":
                assert card.origin_proof is None


async def test_existing_human_card_cannot_be_upgraded_to_routine_authority(
    postgres_raw_db, monkeypatch
):
    from types import SimpleNamespace

    from app.agentive import staging_store
    from app.agentive.services.proposal_origin import origin_for
    from app.agentive.services.routine_proposal_apply import apply_routine_proposal

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "aa" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, entry, thread, _, claimed, ctx = await claimed_with_grant()
        card = await create_staged_change(
            user_id=auth.id,
            workspace_id=workspace.id,
            session_id=thread.id,
            kind="update_entry",
            summary="A human proposal",
            diff_human="A human proposal",
            diff_machine={},
            payload={"entry_id": entry["id"], "title": "Unapproved"},
        )
        proof = origin_for(
            ctx,
            "integral_update_entry",
            claimed.input_payload["routine_mandate_fingerprint"],
        )
        card.origin_proof = proof  # Stale cache/cosmetic persistence is not a grant.
        await staging_store.persist(card)
        private = await staging_store.load_current(card.token)
        assert private is not None and private.origin_proof is None
        before = await postgres_raw_db.get("node", entry["id"])
        result = await apply_routine_proposal(
            SimpleNamespace(
                principal_id=auth.id,
                workspace_id=workspace.id,
                session_id=thread.id,
                work_execution_context=ctx,
            ),
            card.to_dict(),
            proof,
        )
        assert result["state"] == "pending"
        assert await postgres_raw_db.get("node", entry["id"]) == before
