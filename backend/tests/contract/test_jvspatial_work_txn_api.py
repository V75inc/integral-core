"""Contract probes for the durable work-kernel jvspatial transaction API.

Task 0 gate: Postgres transaction handles must expose compare-and-set and
insert-if-absent on the same public handle. Mongo is rejected for Phase B
production certification.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest


def test_postgres_transaction_exposes_cas_and_insert_if_absent() -> None:
    """Public handle must support CAS + insert_if_absent without private access."""
    from jvspatial.db.postgres import PostgresTransaction

    assert hasattr(PostgresTransaction, "find_one_and_update")
    assert callable(PostgresTransaction.find_one_and_update)
    assert hasattr(PostgresTransaction, "insert_if_absent")
    assert callable(PostgresTransaction.insert_if_absent)

    cas_sig = inspect.signature(PostgresTransaction.find_one_and_update)
    assert "collection" in cas_sig.parameters
    assert "query" in cas_sig.parameters
    assert "update" in cas_sig.parameters

    insert_sig = inspect.signature(PostgresTransaction.insert_if_absent)
    assert "collection" in insert_sig.parameters
    assert "data" in insert_sig.parameters


def test_phase_b_rejects_mongo_as_production_work_kernel_store() -> None:
    """Mongo may exist as an adapter, but Phase B work-kernel cert is Postgres-only."""
    from jvspatial.db.mongodb import MongoDB
    from jvspatial.db.transaction import MongoDBTransaction

    assert getattr(MongoDB, "supports_transactions", False) is True
    assert not hasattr(MongoDBTransaction, "find_one_and_update")
    assert not hasattr(MongoDBTransaction, "insert_if_absent")


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_postgres_transaction_cas_probe_against_live_db(
    postgres_raw_db: Any,
) -> None:
    """Live probe: CAS and insert_if_absent share one transaction and roll back."""
    db = postgres_raw_db
    await db.save(
        "o",
        {
            "id": "o.WorkItem.probe",
            "entity": "WorkItem",
            "context": {"status": "queued", "lease_fence": 0},
        },
    )
    txn = await db.begin_transaction()
    updated = await txn.find_one_and_update(
        "o",
        {"id": "o.WorkItem.probe", "context.status": "queued"},
        {"$set": {"context.status": "running", "context.lease_fence": 1}},
    )
    assert updated is not None
    outbox = await txn.insert_if_absent(
        "o",
        {
            "id": "o.WorkOutbox.probe",
            "entity": "WorkOutboxEntry",
            "context": {"work_item_id": "o.WorkItem.probe", "status": "pending"},
        },
    )
    assert outbox.created is True
    await db.rollback_transaction(txn)

    loaded = await db.get("o", "o.WorkItem.probe")
    assert loaded["context"]["status"] == "queued"
    assert await db.get("o", "o.WorkOutbox.probe") is None
