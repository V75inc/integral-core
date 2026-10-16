"""Immutable effect contract evidence is separate from private saved output."""

import pytest
from jvspatial.core import Object
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.services.app_operations import execution_receipts as receipts
from tests.contract.test_operation_outcome_observation_postgres import identity

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]
FP = "a" * 64
OTHER = "b" * 64


class ContractProofEffect(Object):
    """Disposable local effect used to verify receipt rollback."""

    value: str = ""


async def record(database, fingerprint=FP):
    async def effect():
        return {"output": {"private": "never decoded for proof"}}

    return await receipts.execute_operation_once(
        identity=identity(),
        request_hash="input",
        execute=effect,
        database=database,
        effect_contract_fingerprint=fingerprint,
    )


async def test_matching_contract_observation_never_decodes_output_or_changes_row(
    postgres_raw_db,
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        await record(postgres_raw_db)
        key = receipts.receipt_object_id(identity())
        row = await postgres_raw_db.get("object", key)
        assert row["context"]["effect_contract_fingerprint"] == FP
        row["context"]["result_json"] = "malformed private output"
        await postgres_raw_db.save("object", row)
        before = await postgres_raw_db.get("object", key)
        proof = await receipts.lookup_operation_outcome(
            identity=identity(),
            request_hash="input",
            database=postgres_raw_db,
            expected_effect_contract_fingerprint=FP,
        )
        assert proof.identity == identity()
        assert set(proof.reference()) == {"id", "status", "replayed"}
        assert FP not in str(proof.reference())
        assert await postgres_raw_db.get("object", key) == before


@pytest.mark.parametrize("stored", [None, OTHER])
async def test_absent_or_different_contract_cannot_prove_a_reviewed_declaration(
    postgres_raw_db, stored
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        await record(postgres_raw_db, stored)
        key = receipts.receipt_object_id(identity())
        before = await postgres_raw_db.get("object", key)
        expected = (
            receipts.OperationReceiptIncomplete
            if stored is None
            else receipts.OperationReceiptConflict
        )
        with pytest.raises(expected):
            await receipts.lookup_operation_outcome(
                identity=identity(),
                request_hash="input",
                database=postgres_raw_db,
                expected_effect_contract_fingerprint=FP,
            )
        assert await postgres_raw_db.get("object", key) == before


async def test_different_contract_never_reexecutes_an_existing_effect(postgres_raw_db):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        await record(postgres_raw_db)

        async def forbidden():
            pytest.fail("A different reviewed declaration repeated an effect")

        with pytest.raises(receipts.OperationReceiptConflict):
            await receipts.execute_operation_once(
                identity=identity(),
                request_hash="input",
                execute=forbidden,
                database=postgres_raw_db,
                effect_contract_fingerprint=OTHER,
            )
        row = await postgres_raw_db.get(
            "object", receipts.receipt_object_id(identity())
        )
        assert row["context"]["effect_contract_fingerprint"] == FP


async def test_legacy_replay_does_not_guess_or_backfill_contract(postgres_raw_db):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        await record(postgres_raw_db, None)
        before = await postgres_raw_db.get(
            "object", receipts.receipt_object_id(identity())
        )

        async def forbidden():
            pytest.fail("Legacy receipt was replayed as a new effect")

        result = await receipts.execute_operation_once(
            identity=identity(),
            request_hash="input",
            execute=forbidden,
            database=postgres_raw_db,
            effect_contract_fingerprint=FP,
        )
        assert result.replayed
        assert (
            await postgres_raw_db.get("object", receipts.receipt_object_id(identity()))
            == before
        )
        assert "effect_contract_fingerprint" not in before["context"]
        # Legacy past-completion observation remains possible, but not proof of
        # the particular newly reviewed contract tested above.
        assert (
            await receipts.lookup_operation_outcome(
                identity=identity(), request_hash="input", database=postgres_raw_db
            )
            is not None
        )


@pytest.mark.parametrize("bad", [None, "", "not-a-digest", True, "A" * 64])
async def test_corrupt_stored_contract_is_not_legacy_absence(postgres_raw_db, bad):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        await record(postgres_raw_db)
        key = receipts.receipt_object_id(identity())
        row = await postgres_raw_db.get("object", key)
        row["context"]["effect_contract_fingerprint"] = bad
        await postgres_raw_db.save("object", row)
        with pytest.raises(receipts.OperationReceiptIncomplete):
            await receipts.lookup_operation_outcome(
                identity=identity(), request_hash="input", database=postgres_raw_db
            )


async def test_failed_effect_rolls_back_contract_proof_with_its_receipt(
    postgres_raw_db,
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):

        async def fail():
            await ContractProofEffect.create(value="must-roll-back")
            raise RuntimeError("before commit")

        with pytest.raises(RuntimeError, match="before commit"):
            await receipts.execute_operation_once(
                identity=identity(),
                request_hash="input",
                execute=fail,
                database=postgres_raw_db,
                effect_contract_fingerprint=FP,
            )
        assert (
            await postgres_raw_db.get("object", receipts.receipt_object_id(identity()))
            is None
        )
        assert (
            await postgres_raw_db.find(
                "object", {"entity": ContractProofEffect._entity_name()}
            )
            == []
        )


@pytest.mark.parametrize("legacy", [False, True])
async def test_alias_contract_inherits_original_without_legacy_repair(
    postgres_raw_db, legacy
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        original_identity = identity("original-profile")

        async def effect():
            return {"output": {"private": "original"}}

        await receipts.execute_operation_once(
            identity=original_identity,
            request_hash="input",
            execute=effect,
            database=postgres_raw_db,
            effect_contract_fingerprint=None if legacy else FP,
        )
        original_id = receipts.receipt_object_id(original_identity)
        before = await postgres_raw_db.get("object", original_id)

        async def forbidden():
            pytest.fail("Alias was treated as fresh command")

        result = await receipts.execute_operation_once(
            identity=identity(),
            principal_aliases=("original-profile",),
            request_hash="input",
            execute=forbidden,
            database=postgres_raw_db,
            effect_contract_fingerprint=FP,
        )
        assert result.replayed and result.identity == original_identity
        marker = await postgres_raw_db.get(
            "object", receipts.receipt_object_id(identity())
        )
        assert marker["context"]["canonical_receipt_id"] == original_id
        if legacy:
            assert "effect_contract_fingerprint" not in marker["context"]
        else:
            assert marker["context"]["effect_contract_fingerprint"] == FP
        assert await postgres_raw_db.get("object", original_id) == before
        lookup = dict(
            identity=identity(),
            principal_aliases=("original-profile",),
            request_hash="input",
            database=postgres_raw_db,
            expected_effect_contract_fingerprint=FP,
        )
        if legacy:
            with pytest.raises(receipts.OperationReceiptIncomplete):
                await receipts.lookup_operation_outcome(**lookup)
        else:
            proof = await receipts.lookup_operation_outcome(**lookup)
            assert proof.identity == original_identity


async def test_alias_digest_cannot_contradict_its_original_contract(postgres_raw_db):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        await record(postgres_raw_db)
        other = identity("original-profile")
        marker = receipts._receipt_document(
            identity=other,
            request_hash="input",
            status="alias",
            effect_contract_fingerprint=OTHER,
        )
        marker["context"]["canonical_receipt_id"] = receipts.receipt_object_id(
            identity()
        )
        await postgres_raw_db.save("object", marker)
        before = await postgres_raw_db.get("object", marker["id"])
        with pytest.raises(receipts.OperationReceiptIncomplete):
            await receipts.lookup_operation_outcome(
                identity=identity(),
                principal_aliases=("original-profile",),
                request_hash="input",
                database=postgres_raw_db,
            )

        async def forbidden():
            pytest.fail("Corrupt alias entered handler")

        with pytest.raises(receipts.OperationReceiptIncomplete):
            await receipts.execute_operation_once(
                identity=identity(),
                principal_aliases=("original-profile",),
                request_hash="input",
                execute=forbidden,
                database=postgres_raw_db,
                effect_contract_fingerprint=FP,
            )
        assert await postgres_raw_db.get("object", marker["id"]) == before


async def test_actual_dispatch_records_server_bound_declaration_digest(
    postgres_raw_db, monkeypatch
):
    import json

    from tests.contract.test_operation_principal_alias_postgres import (
        test_human_auth_and_graph_aliases_share_one_effect_receipt as execute_real_operation,
    )

    # Reuse the real Auth/profile, canonical definition and Comment mutation
    # fixture; inspect private output only inside this controlled test to check
    # that the independent journal field matches the server-bound marker.
    await execute_real_operation(postgres_raw_db, monkeypatch, False, "execute")
    rows = await postgres_raw_db.find(
        "object", {"entity": "OperationExecutionReceipt", "context.status": "succeeded"}
    )
    assert len(rows) == 1
    row = rows[0]["context"]
    assert (
        row["effect_contract_fingerprint"]
        == json.loads(row["result_json"])["_operation_contract"]
    )
    assert len(row["effect_contract_fingerprint"]) == 64
