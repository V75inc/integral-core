"""WP-00 spike: database-backed conditional state transition via jvspatial.

Proves AC-05 primitive exists on Postgres (find_one_and_update with
predicate). Skip on JSON backend — concurrency guarantees are Postgres-only.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.asyncio,
]


@pytest.fixture
async def db():
    from jvspatial.db.factory import create_database

    kind = (os.getenv("INTEGRAL_TEST_DB") or "json").lower()
    if kind not in ("postgres", "postgresql"):
        pytest.skip("Postgres-only spike (set INTEGRAL_TEST_DB=postgres)")
    database = await create_database()
    yield database
    await database.close()


async def _try_claim(db, doc_id: str) -> bool:
    """Atomically transition state available → claimed."""
    result = await db.find_one_and_update(
        "spike_assets",
        {"_id": doc_id, "state": "available"},
        {"$set": {"state": "claimed"}},
    )
    return result is not None and result.get("state") == "claimed"


@pytest.mark.postgres
async def test_concurrent_claim_exactly_one_wins(db):
    doc_id = f"spike-{uuid.uuid4().hex[:12]}"
    await db.save(
        "spike_assets",
        {"_id": doc_id, "state": "available", "tag": "laptop-1"},
    )

    results = await asyncio.gather(
        _try_claim(db, doc_id),
        _try_claim(db, doc_id),
    )
    assert sorted(results) == [False, True]

    final = await db.find_one("spike_assets", {"_id": doc_id})
    assert final is not None
    assert final["state"] == "claimed"

    await db.delete("spike_assets", doc_id)
