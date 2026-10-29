"""Shared command claims and result receipts on real PostgreSQL connections."""

import asyncio
import uuid
from datetime import timedelta

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.agentive.services.staging_execution import (
    claim_staging_execution,
    execution_record_id,
    mark_staging_external_dispatch,
    record_staging_execution_result,
    staging_execution_effect,
)
from app.agentive.staging import StagingError
from app.services.credential_crypto import decrypt_secret_from_storage

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


def identity():
    return dict(
        token=uuid.uuid4().hex,
        principal_id="principal",
        workspace_id="workspace",
        kind="create_entry",
        payload={"title": "Private result"},
    )


async def test_independent_claims_have_one_owner(postgres_raw_db):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        intent = identity()
        results = await asyncio.gather(
            claim_staging_execution(**intent),
            claim_staging_execution(**intent),
            return_exceptions=True,
        )
        assert sum(not isinstance(r, BaseException) for r in results) == 1
        assert [r.code for r in results if isinstance(r, StagingError)] == [
            "already_executing"
        ]
        with pytest.raises(StagingError, match="identity changed"):
            await claim_staging_execution(**{**intent, "principal_id": "other"})
        with pytest.raises(StagingError, match="identity changed"):
            await claim_staging_execution(**{**intent, "payload": {"title": "Changed"}})


async def test_local_effect_and_encrypted_result_commit_together(
    postgres_raw_db, monkeypatch
):
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "11" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        intent = identity()
        claim = await claim_staging_execution(**intent)
        effect_id = "o.StagingEffectProbe." + intent["token"]
        async with staging_execution_effect(claim) as database:
            await database.insert_if_absent(
                "object",
                {
                    "id": effect_id,
                    "entity": "StagingEffectProbe",
                    "context": {"value": 1},
                },
            )
            await record_staging_execution_result(
                claim=claim,
                database=database,
                result={"id": effect_id, "private": "secret output"},
            )
        assert await postgres_raw_db.get("object", effect_id) is not None
        row = await postgres_raw_db.get("object", execution_record_id(intent["token"]))
        assert row["context"]["state"] == "applied"
        assert "secret output" not in str(row)
        assert "secret output" in decrypt_secret_from_storage(
            row["context"]["result_ciphertext"], aad=claim.object_id
        )
        # A new worker/process reading the store cannot dispatch this token again.
        with pytest.raises(StagingError) as exc:
            await claim_staging_execution(**intent)
        assert exc.value.code == "application_reconciliation_required"


async def test_admitted_effect_can_commit_after_lease_expiry(
    postgres_raw_db, monkeypatch
):
    from app.agentive.services import staging_execution

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "11" * 32)
    now = staging_execution._now()
    monkeypatch.setattr(staging_execution, "_now", lambda: now)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        intent = identity()
        claim = await claim_staging_execution(**intent)
        async with staging_execution_effect(claim) as database:
            now += timedelta(seconds=120)
            await record_staging_execution_result(
                claim=claim, database=database, result={"completed": True}
            )
        row = await postgres_raw_db.get("object", claim.object_id)
        assert row["context"]["state"] == "applied"
        with pytest.raises(StagingError) as exc:
            await claim_staging_execution(**intent)
        assert exc.value.code == "application_reconciliation_required"


async def test_expired_claim_cannot_enter_effect_or_write_unadmitted_receipt(
    postgres_raw_db, monkeypatch
):
    from app.agentive.services import staging_execution
    from app.services.app_operations.transaction_scope import postgres_graph_transaction

    now = staging_execution._now()
    monkeypatch.setattr(staging_execution, "_now", lambda: now)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        claim = await claim_staging_execution(**identity())
        now += timedelta(seconds=120)
        with pytest.raises(StagingError) as exc:
            async with staging_execution_effect(claim):
                pytest.fail("Expired worker entered its effect")
        assert exc.value.code == "execution_claim_lost"
        with pytest.raises(StagingError) as exc:
            async with postgres_graph_transaction() as database:
                await record_staging_execution_result(
                    claim=claim, database=database, result={"unadmitted": True}
                )
        assert exc.value.code == "execution_claim_lost"
        row = await postgres_raw_db.get("object", claim.object_id)
        assert row["context"]["state"] == "claimed"
        assert not row["context"]["result_ciphertext"]


async def test_admitted_effect_still_requires_its_current_owner_and_fence(
    postgres_raw_db, monkeypatch
):
    from app.agentive.services import staging_execution

    now = staging_execution._now()
    monkeypatch.setattr(staging_execution, "_now", lambda: now)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        claim = await claim_staging_execution(**identity())
        with pytest.raises(StagingError) as exc:
            async with staging_execution_effect(claim) as database:
                now += timedelta(seconds=120)
                await database.find_one_and_update(
                    "object", {"id": claim.object_id}, {"$inc": {"context.fence": 1}}
                )
                await record_staging_execution_result(
                    claim=claim, database=database, result={"stale": True}
                )
        assert exc.value.code == "execution_claim_lost"
        row = await postgres_raw_db.get("object", claim.object_id)
        assert row["context"]["fence"] == claim.fence
        assert not row["context"]["result_ciphertext"]


async def test_child_task_cannot_inherit_admitted_effect_lease(
    postgres_raw_db, monkeypatch
):
    from app.agentive.services import staging_execution
    from app.services.app_operations.transaction_scope import (
        OperationTransactionUnavailable,
    )

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "11" * 32)
    now = staging_execution._now()
    monkeypatch.setattr(staging_execution, "_now", lambda: now)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        claim = await claim_staging_execution(**identity())
        async with staging_execution_effect(claim) as database:
            now += timedelta(seconds=120)
            with pytest.raises(OperationTransactionUnavailable):
                await asyncio.create_task(
                    record_staging_execution_result(
                        claim=claim, database=database, result={"inherited": True}
                    )
                )
            await record_staging_execution_result(
                claim=claim, database=database, result={"owner": True}
            )
        row = await postgres_raw_db.get("object", claim.object_id)
        assert row["context"]["state"] == "applied"


async def test_verified_rollback_can_release_expired_unchanged_owner(
    postgres_raw_db, monkeypatch
):
    from app.agentive.services import staging_execution

    now = staging_execution._now()
    monkeypatch.setattr(staging_execution, "_now", lambda: now)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        intent = identity()
        claim = await claim_staging_execution(**intent)
        with pytest.raises(RuntimeError, match="rollback"):
            async with staging_execution_effect(claim):
                now += timedelta(seconds=120)
                raise RuntimeError("rollback")
        await staging_execution.release_unapplied_staging_claim(claim)
        row = await postgres_raw_db.get("object", claim.object_id)
        assert row["context"]["state"] == "ready"
        replacement = await claim_staging_execution(**intent)
        assert replacement.fence == claim.fence + 1
        with pytest.raises(StagingError) as exc:
            await staging_execution.release_unapplied_staging_claim(claim)
        assert exc.value.code == "execution_claim_lost"


async def test_release_keeps_partial_batch_evidence_for_fenced_resume(postgres_raw_db):
    from app.agentive.services import staging_execution
    from app.services.app_operations.transaction_scope import postgres_graph_transaction

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        intent = {**identity(), "kind": "batch"}
        claim = await claim_staging_execution(**intent)
        async with postgres_graph_transaction() as database:
            await database.find_one_and_update(
                "object",
                {"id": claim.object_id},
                {"$set": {"context.local_effect_state": "partial"}},
            )
        await staging_execution.release_unapplied_staging_claim(claim)
        row = await postgres_raw_db.get("object", claim.object_id)
        assert row["context"]["state"] == "ready"
        assert row["context"]["local_effect_state"] == "partial"
        replacement = await claim_staging_execution(**intent)
        assert replacement.fence == claim.fence + 1
        row = await postgres_raw_db.get("object", claim.object_id)
        assert row["context"]["local_effect_state"] == "partial"


@pytest.mark.parametrize("failure", ["missing_receipt", "receipt_write", "cancelled"])
async def test_local_effect_rolls_back_without_a_completed_receipt(
    postgres_raw_db, monkeypatch, failure
):
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "11" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        intent = identity()
        claim = await claim_staging_execution(**intent)
        effect_id = "o.StagingEffectProbe." + intent["token"]
        expected = (
            asyncio.CancelledError
            if failure == "cancelled"
            else (StagingError if failure == "missing_receipt" else OSError)
        )
        with pytest.raises(expected):
            async with staging_execution_effect(claim) as database:
                await database.insert_if_absent(
                    "object",
                    {"id": effect_id, "entity": "StagingEffectProbe", "context": {}},
                )
                if failure == "cancelled":
                    raise asyncio.CancelledError()
                if failure == "receipt_write":
                    original = database.find_one_and_update

                    async def fail_receipt(collection, query, update, **kwargs):
                        if update.get("$set", {}).get("context.state") == "applied":
                            raise OSError("receipt storage failure")
                        return await original(collection, query, update, **kwargs)

                    monkeypatch.setattr(database, "find_one_and_update", fail_receipt)
                    await record_staging_execution_result(
                        claim=claim,
                        database=database,
                        result={"id": effect_id},
                    )
        assert await postgres_raw_db.get("object", effect_id) is None
        row = await postgres_raw_db.get("object", claim.object_id)
        assert row["context"]["state"] == "claimed"
        assert row["context"]["result_ciphertext"] == ""


async def test_expired_undispatched_claim_can_be_replaced_but_old_worker_is_fenced(
    postgres_raw_db,
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        intent = identity()
        old = await claim_staging_execution(**intent, lease_seconds=0.01)
        await asyncio.sleep(0.03)
        current = await claim_staging_execution(**intent)
        assert current.fence == old.fence + 1
        with pytest.raises(StagingError) as exc:
            await mark_staging_external_dispatch(old)
        assert exc.value.code == "execution_claim_lost"
        await mark_staging_external_dispatch(current)


async def test_external_dispatch_intent_is_never_reclaimed_on_expiry(
    postgres_raw_db, monkeypatch
):
    from app.agentive.services import staging_execution

    # Database latency must not expire the claim before dispatch is recorded.
    now = staging_execution._now()
    monkeypatch.setattr(staging_execution, "_now", lambda: now)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        intent = identity()
        claim = await claim_staging_execution(**intent, lease_seconds=30)
        await mark_staging_external_dispatch(claim)
        now += timedelta(seconds=31)
        with pytest.raises(StagingError) as exc:
            await claim_staging_execution(**intent)
        assert exc.value.code == "application_reconciliation_required"
