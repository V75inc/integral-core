"""Full fresh provisioning and search under a genuinely DML-only login."""

import os
import uuid
from urllib.parse import urlsplit, urlunsplit

import pytest


@pytest.mark.asyncio
async def test_fresh_managed_search_and_scope_growth_do_not_build_indexes(
    monkeypatch, tmp_path
):
    import asyncpg

    dsn = os.getenv("STORAGE_INDEX_VECTOR_TEST_DSN")
    if not dsn:
        pytest.skip(
            "STORAGE_INDEX_VECTOR_TEST_DSN must identify disposable pgvector PostgreSQL"
        )
    from app.services.retrieval.index_definition import EmbeddingProfile
    from app.services.retrieval.passage_store import PassageStore
    from app.services.retrieval.pgvector_driver import PgVectorEmbeddingStore
    from app.services.storage_indexes.provision import initial_install

    name = "managed_search_" + uuid.uuid4().hex[:12]
    role = "runtime_" + uuid.uuid4().hex[:12]
    admin = await asyncpg.connect(dsn)
    await admin.execute(f'CREATE DATABASE "{name}"')
    database = urlunsplit(urlsplit(dsn)._replace(path="/" + name))
    driver = None
    try:
        runtime = await initial_install(
            database,
            runtime_role=role,
            runtime_password=uuid.uuid4().hex,
            plan_path=tmp_path / "initial-plan.json",
        )
        monkeypatch.setenv("JVSPATIAL_DB_TYPE", "postgres")
        monkeypatch.setenv("JVSPATIAL_POSTGRES_INDEX_CREATION", "managed")
        monkeypatch.setenv("RETRIEVE_ANN_ENABLED", "1")
        conn = await asyncpg.connect(runtime)
        try:
            count = await conn.fetchval("SELECT count(*) FROM pg_index")
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute("CREATE INDEX forbidden ON node(entity)")
            driver = PgVectorEmbeddingStore(dsn=runtime)
            vector = [1.0] + [0.0] * 383
            for workspace in range(10):
                await driver.upsert(
                    "synthetic-" + str(workspace),
                    vector,
                    {"track_id": "track-" + str(workspace)},
                )
            assert (await driver.search(vector, scope="track:track-0"))[0][
                0
            ] == "synthetic-0"
            await driver.soft_delete("synthetic-0")
            assert await driver.search(vector, scope="track:track-0") == []
            profile = EmbeddingProfile(
                model="synthetic",
                revision="fixture",
                dimensions=384,
                chunk_tokens=128,
                overlap_tokens=16,
            )
            passages = PassageStore(driver, profile)
            assert passages.ann_index_name("workspace-a") == passages.ann_index_name(
                "workspace-b"
            )
            await passages.maintain_selection_indexes()
            assert not await passages.maintain_ann("workspace-a")
            assert await conn.fetchval("SELECT count(*) FROM pg_index") == count
        finally:
            await conn.close()
    finally:
        if driver is not None:
            await driver.close()
        await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        await admin.close()
