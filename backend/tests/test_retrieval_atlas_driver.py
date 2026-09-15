"""Atlas Vector Search integration tests for ``AtlasVectorDriver``.

Wave 4 of the SaaS-deployment plan. Atlas ``$vectorSearch`` cannot be
mocked (``mongomock`` does not implement it), so these tests run against
a real Atlas cluster keyed on ``ATLAS_TEST_URI``. Without the env var,
the whole module is skipped — local ``pytest`` and CI remain green.

Required env::

    ATLAS_TEST_URI       — mongodb+srv://… (a disposable scratch cluster
                           or a database scoped to test data)
    ATLAS_TEST_DB        — database name; defaults to ``integral_test``
    ATLAS_TEST_COLLECTION — collection name; defaults to ``entry_embeddings_test``
    ATLAS_TEST_INDEX     — vector search index name; defaults to
                           ``entry_embedding_vector_idx``

The test collection MUST already have an ``entry_embedding_vector_idx``
vectorSearch index (numDimensions=384, similarity=cosine, with
``track_id`` + ``deleted`` as filter fields) — Atlas indexes are
provisioned out-of-band and hydrate asynchronously, so creating them
inside a test would race.

Cleanup discipline: each test runs against a unique track_id prefix so
parallel runs do not collide; the suite drops its own rows on teardown.
"""

from __future__ import annotations

import os
import uuid

import pytest

pytestmark = [pytest.mark.atlas, pytest.mark.integration]

_ATLAS_URI = os.environ.get("ATLAS_TEST_URI")

if not _ATLAS_URI:  # pragma: no cover — skip path is the no-op success
    pytest.skip(
        "ATLAS_TEST_URI not set; AtlasVectorDriver integration tests skipped",
        allow_module_level=True,
    )


def _make_vec(seed: int, dim: int = 384) -> list[float]:
    import random

    rnd = random.Random(seed)
    # Slight bias so the unit-norm vectors don't all collapse to zero
    # under cosine similarity on noisy ANN candidates.
    return [rnd.uniform(-1.0, 1.0) for _ in range(dim)]


@pytest.fixture
def atlas_driver():
    from app.services.retrieval.atlas_vector_driver import AtlasVectorDriver

    drv = AtlasVectorDriver(
        uri=_ATLAS_URI,
        db_name=os.environ.get("ATLAS_TEST_DB", "integral_test"),
        collection=os.environ.get("ATLAS_TEST_COLLECTION", "entry_embeddings_test"),
        index_name=os.environ.get("ATLAS_TEST_INDEX", "entry_embedding_vector_idx"),
    )
    yield drv


@pytest.fixture
def track_namespace() -> str:
    """Unique track-id prefix so parallel runs do not collide."""
    return f"test:{uuid.uuid4().hex[:8]}"


@pytest.mark.asyncio
async def test_upsert_and_search_round_trip(atlas_driver, track_namespace):
    """upsert(entry) → search(vector) surfaces it; scope filter narrows results."""
    v1 = _make_vec(1)
    v2 = _make_vec(2)
    v3 = _make_vec(3)

    e1 = f"e1_{uuid.uuid4().hex[:8]}"
    e2 = f"e2_{uuid.uuid4().hex[:8]}"
    e3 = f"e3_{uuid.uuid4().hex[:8]}"

    try:
        await atlas_driver.upsert(e1, v1, {"track_id": f"{track_namespace}_A"})
        await atlas_driver.upsert(e2, v2, {"track_id": f"{track_namespace}_A"})
        await atlas_driver.upsert(e3, v3, {"track_id": f"{track_namespace}_B"})

        # Atlas search indexes hydrate asynchronously — the test cluster
        # is expected to be primed with the index, but newly-upserted
        # docs may not be searchable for a few seconds. Give the index
        # time to absorb the writes before asserting.
        await _wait_for_searchable(atlas_driver, v1, e1)

        # Universe-wide search — e1 must surface (top-ranked vs itself).
        results = await atlas_driver.search(v1, k=10)
        ids = {row[0] for row in results}
        assert e1 in ids

        # Scope pre-filter on track_id MUST drop e3.
        scoped = await atlas_driver.search(v1, k=10, scope=f"track:{track_namespace}_A")
        scoped_ids = {row[0] for row in scoped}
        assert e3 not in scoped_ids
        assert e1 in scoped_ids
    finally:
        for entry_id in (e1, e2, e3):
            await atlas_driver.hard_delete(entry_id)


@pytest.mark.asyncio
async def test_soft_delete_excludes_from_search(atlas_driver, track_namespace):
    """soft_delete flips deleted=true; subsequent search MUST exclude the row."""
    v = _make_vec(7)
    entry_id = f"ghost_{uuid.uuid4().hex[:8]}"

    try:
        await atlas_driver.upsert(entry_id, v, {"track_id": f"{track_namespace}_G"})
        await _wait_for_searchable(atlas_driver, v, entry_id)

        pre = await atlas_driver.search(v, k=10)
        assert any(row[0] == entry_id for row in pre)

        await atlas_driver.soft_delete(entry_id)
        await _wait_for_excluded(atlas_driver, v, entry_id)

        post = await atlas_driver.search(v, k=10)
        assert not any(row[0] == entry_id for row in post)
    finally:
        await atlas_driver.hard_delete(entry_id)


@pytest.mark.asyncio
async def test_dim_mismatch_rejected(atlas_driver, track_namespace):
    """upsert with a 256-dim vector raises ValueError (384 expected)."""
    with pytest.raises(ValueError, match="dim mismatch"):
        await atlas_driver.upsert(
            "x", [0.0] * 256, {"track_id": f"{track_namespace}_x"}
        )


@pytest.mark.asyncio
async def test_track_id_metadata_required(atlas_driver):
    """upsert without metadata['track_id'] raises ValueError."""
    with pytest.raises(ValueError, match="track_id is required"):
        await atlas_driver.upsert("x", _make_vec(1), {})


# ---------------------------------------------------------------------------
# Helpers — Atlas search indexes hydrate asynchronously; the test cluster
# is expected to be primed with the index, but new docs may take a few
# seconds to be searchable. We retry-with-backoff up to a hard ceiling so
# CI stays bounded.
# ---------------------------------------------------------------------------


async def _wait_for_searchable(driver, query_vec, entry_id, *, attempts: int = 20):
    import asyncio

    for _ in range(attempts):
        rows = await driver.search(query_vec, k=20)
        if any(row[0] == entry_id for row in rows):
            return
        await asyncio.sleep(0.5)
    raise AssertionError(
        f"entry {entry_id} did not become searchable within {attempts * 0.5:.1f}s"
    )


async def _wait_for_excluded(driver, query_vec, entry_id, *, attempts: int = 20):
    import asyncio

    for _ in range(attempts):
        rows = await driver.search(query_vec, k=20)
        if not any(row[0] == entry_id for row in rows):
            return
        await asyncio.sleep(0.5)
    raise AssertionError(
        f"entry {entry_id} still searchable after soft_delete within "
        f"{attempts * 0.5:.1f}s"
    )
