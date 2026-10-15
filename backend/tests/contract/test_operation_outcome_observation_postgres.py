"""Read-only receipt observation never claims or reconstructs operation effects."""

import asyncio
from contextvars import Context
from dataclasses import asdict

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.contracts.operations import OperationIdentity
from app.services.app_operations import execution_receipts as receipts

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


def identity(principal="test-subject"):
    return OperationIdentity.create(
        workspace_id="test-workspace",
        app_id="test-app",
        operation_key="test-command",
        principal_id=principal,
        idempotency_key="private-test-key",
    )


async def observe(
    database, *, principal="test-subject", aliases=(), request_hash="input"
):
    return await receipts.lookup_operation_outcome(
        identity=identity(principal),
        principal_aliases=aliases,
        request_hash=request_hash,
        database=database,
    )


async def save(database, *, principal="test-subject", status="succeeded", **updates):
    row = receipts._receipt_document(
        identity=identity(principal),
        request_hash="input",
        status=status,
        result_json='{"output":{"private":"hidden-data"}}',
    )
    row["context"].update(updates)
    await database.save("object", row)
    return row


async def test_absent_outcome_does_not_create_a_claim(postgres_raw_db):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        assert await observe(postgres_raw_db) is None
        assert (
            await postgres_raw_db.get("object", receipts.receipt_object_id(identity()))
            is None
        )


@pytest.mark.parametrize("original", ["test-subject", "linked-profile"])
async def test_observation_preserves_original_identity_without_alias_backfill(
    postgres_raw_db, original
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        before = await save(postgres_raw_db, principal=original)
        result = await observe(postgres_raw_db, aliases=("linked-profile",))
        assert asdict(result) == {"identity": asdict(identity(original))}
        assert result.reference() == {
            "id": before["id"],
            "status": "succeeded",
            "replayed": True,
        }
        assert not hasattr(result, "result")
        assert await postgres_raw_db.get("object", before["id"]) == before
        absent = identity(
            "linked-profile" if original == "test-subject" else "test-subject"
        )
        assert (
            await postgres_raw_db.get("object", receipts.receipt_object_id(absent))
            is None
        )


async def test_direct_alias_marker_resolves_original_without_writes(postgres_raw_db):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        original = await save(postgres_raw_db, principal="linked-profile")
        marker = await save(
            postgres_raw_db,
            status="alias",
            result_json="",
            canonical_receipt_id=original["id"],
        )
        result = await observe(postgres_raw_db, aliases=("linked-profile",))
        assert result.reference()["id"] == original["id"]
        assert await postgres_raw_db.get("object", marker["id"]) == marker


@pytest.mark.parametrize("status", ["claimed", "failed", "unknown", "alias"])
async def test_incomplete_history_is_not_success_or_absence(postgres_raw_db, status):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        await save(postgres_raw_db, status=status)
        with pytest.raises(receipts.OperationReceiptIncomplete):
            await observe(postgres_raw_db)


@pytest.mark.parametrize(
    "field",
    ["workspace_id", "app_id", "operation_key", "principal_id", "idempotency_key"],
)
async def test_corrupt_identity_is_not_observed(postgres_raw_db, field):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        await save(postgres_raw_db, **{field: "foreign-value"})
        with pytest.raises(receipts.OperationReceiptIncomplete):
            await observe(postgres_raw_db)


async def test_changed_request_keeps_existing_conflict_semantics(postgres_raw_db):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        await save(postgres_raw_db)
        with pytest.raises(receipts.OperationReceiptConflict):
            await observe(postgres_raw_db, request_hash="different-input")


async def test_multiple_completed_aliases_require_reconciliation(postgres_raw_db):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        await save(postgres_raw_db)
        await save(postgres_raw_db, principal="linked-profile")
        with pytest.raises(receipts.OperationReceiptIncomplete):
            await observe(postgres_raw_db, aliases=("linked-profile",))


@pytest.mark.parametrize("target", ["foreign-receipt", "self"])
async def test_marker_cannot_point_outside_exact_original(postgres_raw_db, target):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        await save(postgres_raw_db, principal="linked-profile")
        await save(
            postgres_raw_db,
            status="alias",
            result_json="",
            canonical_receipt_id=(
                receipts.receipt_object_id(identity()) if target == "self" else target
            ),
        )
        with pytest.raises(receipts.OperationReceiptIncomplete):
            await observe(postgres_raw_db, aliases=("linked-profile",))


async def test_recorded_completion_does_not_deserialize_private_output(postgres_raw_db):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        row = await save(postgres_raw_db, result_json="not-parseable-private-result")
        result = await observe(postgres_raw_db)
        assert result.reference()["id"] == row["id"]
        assert set(asdict(result)) == {"identity"}
        assert "not-parseable-private-result" not in str(result.reference())
        assert "private-test-key" not in str(result.reference())
        assert await postgres_raw_db.get("object", row["id"]) == row


async def test_other_principal_does_not_observe_a_shared_key(postgres_raw_db):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        await save(postgres_raw_db)
        assert await observe(postgres_raw_db, principal="other-subject") is None


@pytest.mark.parametrize("aliases", [("one", "two"), (" invalid ",), (None,)])
async def test_unverified_alias_sets_are_rejected(postgres_raw_db, aliases):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        with pytest.raises(ValueError):
            await observe(postgres_raw_db, aliases=aliases)


async def test_lookup_is_one_bounded_read_without_claim_or_alias_writes(
    postgres_raw_db, monkeypatch
):
    from jvspatial.db.postgres import PostgresTransaction

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        original = await save(postgres_raw_db, principal="linked-profile")
        reads = []
        original_read = PostgresTransaction.find_many

        async def read(transaction, collection, ids):
            reads.append((collection, ids))
            return await original_read(transaction, collection, ids)

        async def forbidden(*args, **kwargs):
            pytest.fail("Outcome observation must never write or claim")

        monkeypatch.setattr(PostgresTransaction, "find_many", read)
        for method in ["save", "insert_if_absent", "find_one_and_update", "delete"]:
            monkeypatch.setattr(PostgresTransaction, method, forbidden)
        result = await observe(postgres_raw_db, aliases=("linked-profile",))
        assert reads == [
            ("object", sorted([original["id"], receipts.receipt_object_id(identity())]))
        ]
        assert set(result.reference()) == {"id", "status", "replayed"}
        assert "private-test-key" not in repr(result)
        assert await postgres_raw_db.get("object", original["id"]) == original
        assert (
            await postgres_raw_db.get("object", receipts.receipt_object_id(identity()))
            is None
        )


async def test_outage_is_not_an_absent_or_successful_outcome(
    postgres_raw_db, monkeypatch
):
    from jvspatial.db.postgres import PostgresTransaction

    async def unavailable(*args, **kwargs):
        raise ConnectionError("test journal outage")

    monkeypatch.setattr(PostgresTransaction, "find_many", unavailable)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        with pytest.raises(ConnectionError, match="test journal outage"):
            await observe(postgres_raw_db)


@pytest.mark.parametrize("corruption", ["entity", "alias_output", "alias_hash"])
async def test_unsupported_receipt_evidence_stays_ambiguous(
    postgres_raw_db, corruption
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        original = await save(postgres_raw_db, principal="linked-profile")
        marker = await save(
            postgres_raw_db,
            status="alias",
            result_json="",
            canonical_receipt_id=original["id"],
        )
        if corruption == "entity":
            original["entity"] = "OtherObject"
            await postgres_raw_db.save("object", original)
        else:
            marker["context"][
                "result_json" if corruption == "alias_output" else "request_hash"
            ] = "different"
            await postgres_raw_db.save("object", marker)
        error = (
            receipts.OperationReceiptConflict
            if corruption == "alias_hash"
            else receipts.OperationReceiptIncomplete
        )
        with pytest.raises(error):
            await observe(postgres_raw_db, aliases=("linked-profile",))


@pytest.mark.parametrize("commit", [True, False])
async def test_independent_observer_never_treats_inflight_or_rolled_back_work_as_success(
    postgres_raw_db, commit
):
    # Production readiness creates the journal collection before accepting work.
    # Do not test visibility through an unrelated, still-uncommitted DDL lock.
    await postgres_raw_db.get("object", "missing-test-journal-row")
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def write():
        async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):

            async def handler():
                nonlocal calls
                calls += 1
                entered.set()
                await release.wait()
                return {"output": {"private": "not observed"}}

            return await receipts.execute_operation_once(
                identity=identity(),
                request_hash="input",
                execute=handler,
                database=postgres_raw_db,
            )

    # A distinct task/context must acquire its own native connection.
    writer = asyncio.create_task(write(), context=Context())
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
            assert await asyncio.wait_for(observe(postgres_raw_db), timeout=5) is None
        if commit:
            release.set()
            await asyncio.wait_for(writer, timeout=5)
        else:
            writer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await writer
        async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
            outcome = await observe(postgres_raw_db)
            assert bool(outcome) is commit
            if outcome:
                assert outcome.reference()["status"] == "succeeded"
                assert not hasattr(outcome, "result")
        assert calls == 1
    finally:
        if not writer.done():
            writer.cancel()
        await asyncio.gather(writer, return_exceptions=True)
