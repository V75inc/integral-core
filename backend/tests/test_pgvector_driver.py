"""Integration tests for ``PgVectorEmbeddingStore`` (pgvector driver).

These exercise the real ``vector`` extension on a live Postgres, so the
whole module is skipped unless ``JVSPATIAL_POSTGRES_DSN`` points at a
reachable Postgres with the ``vector`` extension available. Local
``pytest`` and CI without a database remain green via the module-level
skip.

Isolation: every test runs against a UNIQUE test-scoped table
(``entry_embedding_test_<uuid>``) created via the driver's ``table``
constructor param, and the table is dropped on teardown — no pollution of
the real ``entry_embedding`` table.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest

pytestmark = [pytest.mark.integration]


_PG_DSN = os.environ.get(
    "JVSPATIAL_POSTGRES_DSN",
    "postgresql://integral:integral@localhost:5433/integral",
)


def _pg_available() -> bool:
    """Connectivity + extension probe — drives the module-level skip."""

    async def _probe() -> bool:
        try:
            import asyncpg
        except Exception:
            return False
        try:
            conn = await asyncpg.connect(_PG_DSN, timeout=4)
        except Exception:
            return False
        try:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            return True
        except Exception:
            return False
        finally:
            await conn.close()

    try:
        return asyncio.get_event_loop().run_until_complete(_probe())
    except RuntimeError:
        # No usable loop (already running / closed) — spin a fresh one.
        return asyncio.run(_probe())


if not _pg_available():  # pragma: no cover — skip path is the no-op success
    pytest.skip(
        "JVSPATIAL_POSTGRES_DSN not reachable / vector ext unavailable; "
        "pgvector driver tests skipped",
        allow_module_level=True,
    )


def _make_vec(seed: int, dim: int = 384) -> list[float]:
    """Deterministic pseudo-random vector for tests."""

    import random

    rnd = random.Random(seed)
    return [rnd.uniform(-1.0, 1.0) for _ in range(dim)]


def _near(vec: list[float], jitter: float = 1e-3) -> list[float]:
    """A vector very close to ``vec`` (so cosine similarity ≈ 1)."""

    import random

    rnd = random.Random(999)
    return [x + rnd.uniform(-jitter, jitter) for x in vec]


@pytest.fixture
async def driver():
    """Driver bound to a unique, disposable test table; dropped on teardown."""

    from app.services.retrieval.pgvector_driver import PgVectorEmbeddingStore

    table = f"entry_embedding_test_{uuid.uuid4().hex[:12]}"
    drv = PgVectorEmbeddingStore(dsn=_PG_DSN, table=table)
    await drv.ensure_schema()
    try:
        yield drv
    finally:
        # Drop the table (and indexes cascade with it) then close the pool.
        try:
            pool = await drv._get_pool()
            async with pool.acquire() as conn:
                await conn.execute(f"DROP TABLE IF EXISTS {table}")
        finally:
            await drv.close()


@pytest.mark.asyncio
async def test_upsert_search_roundtrip_ranks_closest_first(driver):
    """upsert 3 vectors; a query close to one ranks that entry first."""

    v1 = _make_vec(1)
    v2 = _make_vec(2)
    v3 = _make_vec(3)

    await driver.upsert("e1", v1, {"track_id": "t1"})
    await driver.upsert("e2", v2, {"track_id": "t1"})
    await driver.upsert("e3", v3, {"track_id": "t1"})

    results = await driver.search(_near(v2), k=3)

    assert results, "expected at least one candidate"
    # Returns (entry_id, similarity) tuples, similarity descending.
    assert results[0][0] == "e2"
    sims = [s for _, s in results]
    assert sims == sorted(sims, reverse=True)
    # Candidate generation only — no permission scoping at this layer.
    assert all(isinstance(eid, str) and isinstance(s, float) for eid, s in results)


@pytest.mark.asyncio
async def test_upsert_dim_mismatch_raises_value_error(driver):
    """A vector of the wrong length raises ValueError (Protocol contract)."""

    with pytest.raises(ValueError):
        await driver.upsert("e_bad", [0.1, 0.2, 0.3], {"track_id": "t1"})


@pytest.mark.asyncio
async def test_search_dim_mismatch_raises_value_error(driver):
    with pytest.raises(ValueError):
        await driver.search([0.1, 0.2, 0.3], k=5)


@pytest.mark.asyncio
async def test_upsert_requires_track_id(driver):
    with pytest.raises(ValueError):
        await driver.upsert("e_notrack", _make_vec(1), {})


@pytest.mark.asyncio
async def test_soft_delete_excludes_then_reupsert_undeletes(driver):
    """soft_delete excludes from search; a fresh upsert clears deleted_at."""

    v1 = _make_vec(10)
    await driver.upsert("e1", v1, {"track_id": "t1"})

    # Present before delete.
    before = await driver.search(_near(v1), k=5)
    assert any(eid == "e1" for eid, _ in before)

    await driver.soft_delete("e1")
    after = await driver.search(_near(v1), k=5)
    assert all(eid != "e1" for eid, _ in after)
    assert await driver.count() == 0  # count excludes soft-deleted

    # Idempotent — soft-deleting again / a non-existent row does not raise.
    await driver.soft_delete("e1")
    await driver.soft_delete("ghost")

    # Re-upsert un-deletes (deleted_at = NULL).
    await driver.upsert("e1", v1, {"track_id": "t1"})
    revived = await driver.search(_near(v1), k=5)
    assert any(eid == "e1" for eid, _ in revived)
    assert await driver.count() == 1


@pytest.mark.asyncio
async def test_track_scope_prefilter(driver):
    """scope='track:t1' returns only that track's rows."""

    await driver.upsert("a1", _make_vec(1), {"track_id": "t1"})
    await driver.upsert("a2", _make_vec(2), {"track_id": "t1"})
    await driver.upsert("b1", _make_vec(3), {"track_id": "t2"})

    # Query vector arbitrary — the assertion is purely about the scope filter.
    q = _make_vec(1)

    scoped = await driver.search(q, k=10, scope="track:t1")
    ids = {eid for eid, _ in scoped}
    assert ids == {"a1", "a2"}

    # workspace scope = no pre-filter (policy filter authoritative downstream).
    unscoped = await driver.search(q, k=10, scope="workspace:w1")
    all_ids = {eid for eid, _ in unscoped}
    assert {"a1", "a2", "b1"}.issubset(all_ids)

    # None scope also has no pre-filter.
    none_scoped = await driver.search(q, k=10, scope=None)
    assert {"a1", "a2", "b1"}.issubset({eid for eid, _ in none_scoped})


@pytest.mark.asyncio
async def test_hard_delete_removes_row(driver):
    v1 = _make_vec(20)
    await driver.upsert("h1", v1, {"track_id": "t1"})
    assert await driver.count() == 1
    await driver.hard_delete("h1")
    assert await driver.count() == 0
    gone = await driver.search(_near(v1), k=5)
    assert all(eid != "h1" for eid, _ in gone)


@pytest.mark.asyncio
async def test_type_id_persisted_and_optional(driver):
    """type_id is persisted when present and tolerated when absent."""

    await driver.upsert("with_type", _make_vec(1), {"track_id": "t1", "type_id": "ty1"})
    await driver.upsert("no_type", _make_vec(2), {"track_id": "t1"})

    pool = await driver._get_pool()
    async with pool.acquire() as conn:
        with_type = await conn.fetchval(
            f"SELECT type_id FROM {driver._table} WHERE entry_id = 'with_type'"
        )
        no_type = await conn.fetchval(
            f"SELECT type_id FROM {driver._table} WHERE entry_id = 'no_type'"
        )
    assert with_type == "ty1"
    assert no_type is None


def test_resolve_driver_name_auto_postgres(monkeypatch):
    """_resolve_driver_name() → 'pgvector' under auto + Postgres + registered."""

    import app.services.retrieval as retrieval

    monkeypatch.setenv("EMBEDDING_STORE_DRIVER", "auto")
    monkeypatch.setenv("JVSPATIAL_DB_TYPE", "postgres")
    monkeypatch.setenv("JVSPATIAL_POSTGRES_DSN", _PG_DSN)

    # Ensure the driver is registered for this assertion regardless of the
    # process's import-time env (conftest may have imported under json mode).
    if "pgvector" not in retrieval.get_registered_drivers():
        from app.services.retrieval.pgvector_driver import PgVectorEmbeddingStore

        retrieval.register_driver("pgvector", PgVectorEmbeddingStore(dsn=_PG_DSN))

    assert retrieval._resolve_driver_name() == "pgvector"
    assert "pgvector" in retrieval.get_registered_drivers()
    assert retrieval.semantic_retrieval_available() is True
