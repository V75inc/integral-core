"""WP-00 spike: database-backed conditional state transition via jvspatial.

Proves AC-05 primitive exists on Postgres (find_one_and_update with
predicate). Skip on JSON backend — concurrency guarantees are Postgres-only.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.asyncio,
]


async def _try_claim(db, doc_id: str) -> bool:
    """Atomically transition state available → claimed."""
    result = await db.find_one_and_update(
        "spike_assets",
        {"_id": doc_id, "state": "available"},
        {"$set": {"state": "claimed"}},
    )
    return result is not None and result.get("state") == "claimed"


@pytest.mark.postgres
async def test_concurrent_claim_exactly_one_wins(postgres_raw_db):
    doc_id = f"spike-{uuid.uuid4().hex[:12]}"
    await postgres_raw_db.save(
        "spike_assets",
        {"_id": doc_id, "state": "available", "tag": "laptop-1"},
    )

    results = await asyncio.gather(
        _try_claim(postgres_raw_db, doc_id),
        _try_claim(postgres_raw_db, doc_id),
    )
    assert sorted(results) == [False, True]

    final = await postgres_raw_db.find_one("spike_assets", {"_id": doc_id})
    assert final is not None
    assert final["state"] == "claimed"

    await postgres_raw_db.delete("spike_assets", doc_id)
