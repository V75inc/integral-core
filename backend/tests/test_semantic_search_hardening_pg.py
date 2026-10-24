"""Real storage regressions for repair, independent lexical search and ANN."""

import pytest

from app.services.retrieval import embedding_model, indexing
from app.services.retrieval.index_definition import embedding_profile
from app.services.retrieval.passage_store import passage_store
from tests.test_semantic_indexing_pg import indexed_graph, search, vector_driver

pytestmark = [pytest.mark.integration, pytest.mark.slow, pytest.mark.asyncio]


async def test_missing_passage_is_repaired_without_source_edit(vector_driver):
    user, workspace, app, track, entry = await indexed_graph(vector_driver)
    store = passage_store(vector_driver, embedding_profile())
    pool = await store.ensure_schema()
    async with pool.acquire() as conn:
        await conn.execute(
            f"DELETE FROM {store.table} WHERE entry_id=$1 AND ordinal=0", entry.id
        )
    assert not await store.integrity(
        entry.id, (await indexing.index_document(entry))["source_hash"]
    )
    await indexing.reconcile_page()
    assert (await indexing.deliver_index_jobs())["indexed"] == 1
    assert await store.integrity(
        entry.id, (await indexing.index_document(entry))["source_hash"]
    )
    assert (await search(user, workspace, app, vector_driver))[0]


async def test_changed_document_reuses_verified_unchanged_passages(
    vector_driver, monkeypatch
):
    user, workspace, app, track, entry = await indexed_graph(vector_driver)
    calls = []
    original = embedding_model.encode_passages

    async def measured(texts, dimensions):
        calls.extend(texts)
        return await original(texts, dimensions)

    monkeypatch.setattr(embedding_model, "encode_passages", measured)
    entry.title = "Updated equipment safety title"
    await entry.save()
    await indexing.enqueue_entry(entry)
    assert (await indexing.deliver_index_jobs())["indexed"] == 1
    assert calls == [entry.title]


async def test_failed_encoder_still_publishes_current_lexical_evidence(
    vector_driver, monkeypatch
):
    user, workspace, app, track, entry = await indexed_graph(vector_driver)
    entry.body = "The emergency assembly location is the eastern courtyard."
    await entry.save()
    await indexing.enqueue_entry(entry)

    async def unavailable(*args):
        raise RuntimeError("Encoder offline")

    monkeypatch.setattr(embedding_model, "encode_passages", unavailable)
    assert (await indexing.deliver_index_jobs())["failed"] == 1
    results, *_ = await search(
        user, workspace, app, vector_driver, query="eastern courtyard", mode="graph"
    )
    assert results and "eastern courtyard" in results[0].snippet


async def test_status_never_calls_provider_and_detects_partial_corruption(
    vector_driver, monkeypatch
):
    from app.services.retrieval import profile_encoder
    from app.services.retrieval.index_management import index_status

    user, workspace, app, track, entry = await indexed_graph(vector_driver)

    async def forbidden(*args, **kwargs):
        raise AssertionError("Status must not invoke inference")

    monkeypatch.setattr(profile_encoder, "encode_profile", forbidden)
    assert (await index_status(user.id, track.id, workspace.id))["coverage_complete"]
    store = passage_store(vector_driver, embedding_profile())
    pool = await store.ensure_schema()
    async with pool.acquire() as conn:
        await conn.execute(
            f"UPDATE {store.table} SET content='corrupt' WHERE entry_id=$1 AND ordinal=1",
            entry.id,
        )
    assert not (await index_status(user.id, track.id, workspace.id))[
        "coverage_complete"
    ]


async def test_lexical_corruption_is_not_reported_as_complete(vector_driver):
    from app.services.retrieval.index_management import index_status

    user, workspace, app, track, entry = await indexed_graph(vector_driver)
    store = passage_store(vector_driver, embedding_profile())
    pool = await store.ensure_schema()
    async with pool.acquire() as conn:
        await conn.execute(
            f"DELETE FROM {store.lexical} WHERE entry_id=$1 AND ordinal=0", entry.id
        )
    assert not (await index_status(user.id, track.id, workspace.id))[
        "coverage_complete"
    ]
    await indexing.reconcile_page()
    assert (await indexing.deliver_index_jobs())["indexed"] == 1
    assert (await index_status(user.id, track.id, workspace.id))["coverage_complete"]


@pytest.mark.parametrize("dimensions", [16, 384])
async def test_scoped_ann_recall_against_exact_and_bounded_index_count(
    vector_driver, monkeypatch, dimensions
):
    """Compare the real ANN path at workspace, Track and explicit-ID selectivity."""
    import json
    from dataclasses import replace

    import numpy as np

    from app.services.retrieval.pgvector_driver import _vector_literal

    profile = replace(embedding_profile(), dimensions=dimensions)
    store = passage_store(vector_driver, profile)
    pool = await store.ensure_schema()
    rng = np.random.default_rng(1729)
    vectors = rng.normal(size=(6000, dimensions))
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    async with pool.acquire() as conn:
        await conn.executemany(
            f"INSERT INTO {store.table}(entry_id,profile_id,ordinal,track_id,workspace_id,app_id,source_hash,source_revision,field_path,start_offset,end_offset,content,embedding) VALUES($1,$2,0,$3,$4,'a','hash',1,'body',0,4,'text',$5::vector)",
            [
                (
                    f"e{i:05}",
                    profile.id,
                    f"t{i%4}",
                    "w1" if i < 3000 else "w2",
                    _vector_literal(v),
                )
                for i, v in enumerate(vectors)
            ],
        )
        await conn.execute(f"ANALYZE {store.table}")
    monkeypatch.setenv("RETRIEVE_EXACT_THRESHOLD", "100")
    monkeypatch.setenv("RETRIEVE_ANN_ENABLED", "1")
    assert await store.maintain_ann("w1")
    monkeypatch.setenv("RETRIEVE_MAX_ANN_INDEXES", "1")
    assert not await store.maintain_ann("w2")
    recalls = []
    for tracks, selected in [
        (["t0", "t1", "t2", "t3"], None),
        (["t0"], None),
        (["t0", "t1"], [f"e{i:05}" for i in range(800)]),
    ]:
        for q in vectors[::700][:6]:
            monkeypatch.setenv("RETRIEVE_EXACT_THRESHOLD", "100000")
            exact = await store.search(
                q.tolist(),
                workspace_id="w1",
                track_ids=tracks,
                k=20,
                eligible_entry_ids=selected,
            )
            monkeypatch.setenv("RETRIEVE_EXACT_THRESHOLD", "100")
            approximate = await store.search(
                q.tolist(),
                workspace_id="w1",
                track_ids=tracks,
                k=20,
                eligible_entry_ids=selected,
            )
            expected = {r["entry_id"] for r in exact}
            recalls.append(
                len(expected & {r["entry_id"] for r in approximate}) / len(expected)
            )
            assert all(int(r["entry_id"][1:]) < 3000 for r in approximate)
    assert min(recalls) >= 0.95, recalls
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SET LOCAL enable_seqscan=off")
        plan = await conn.fetchval(
            f"EXPLAIN (FORMAT JSON) SELECT entry_id FROM {store.table} WHERE profile_id='{profile.id}' AND workspace_id='w1' AND deleted_at IS NULL ORDER BY embedding::vector({dimensions}) <=> $1::vector({dimensions}) LIMIT 20",
            _vector_literal(vectors[0]),
        )
        assert store.ann_index_name("w1") in str(json.loads(plan))


async def test_caller_owned_transaction_keeps_checks_on_owner_task(
    vector_driver, monkeypatch
):
    import asyncio

    from app.services import entry_write_scope
    from app.services.retrieval import search_service

    user, workspace, app, track, entry = await indexed_graph(vector_driver)
    caller = asyncio.current_task()
    original = search_service.evaluate

    async def checked(*args, **kwargs):
        assert asyncio.current_task() is caller
        return await original(*args, **kwargs)

    monkeypatch.setattr(
        entry_write_scope, "is_postgres_entry_transaction", lambda db: True
    )
    monkeypatch.setattr(search_service, "evaluate", checked)
    assert (await search(user, workspace, app, vector_driver))[0]


async def test_invalid_deployment_profile_preserves_canonical_fallback(
    vector_driver, monkeypatch
):
    from app.api import retrieve as handler
    from app.api.retrieve import _dispatch_retrieve_modes
    from app.schemas.policy import Subject
    from app.schemas.retrieve import RetrieveRequest
    from app.services.agent_scope import current_scope_workspace_id

    user, workspace, app, track, entry = await indexed_graph(vector_driver)
    monkeypatch.setattr(handler, "get_embedding_store", lambda: vector_driver)
    monkeypatch.setenv("EMBEDDING_MODEL_DIMENSIONS", "10000")
    token = current_scope_workspace_id.set(workspace.id)
    try:
        found, dropped, examined, mode, degraded = await _dispatch_retrieve_modes(
            Subject(kind="human", id=user.id),
            RetrieveRequest(
                query="lithium batteries", scope=f"app:{app.id}", mode="semantic"
            ),
            requested_mode="semantic",
            effective_mode="semantic",
            degraded=False,
            k=150,
            top_n=5,
        )
        assert degraded and mode == "graph"
        assert any(result.entry_id == entry.id for result in found)
    finally:
        current_scope_workspace_id.reset(token)
