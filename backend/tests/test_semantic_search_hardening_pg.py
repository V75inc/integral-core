"""Real storage regressions for repair, independent lexical search and ANN."""

import pytest

from app.services.retrieval import embedding_model, indexing
from app.services.retrieval.index_definition import embedding_profile
from app.services.retrieval.passage_store import passage_store
from tests.test_semantic_indexing_pg import indexed_graph, search, vector_driver

pytestmark = [pytest.mark.integration, pytest.mark.slow, pytest.mark.asyncio]


async def test_readable_scope_never_enumerates_redundant_record_grants(
    vector_driver, monkeypatch
):
    from app.models.edges import OWNS
    from app.services.retrieval import search_service

    user, workspace, app, track, entry = await indexed_graph(vector_driver)
    await user.connect(entry, edge=OWNS)

    async def forbidden(*args, **kwargs):
        raise AssertionError("Readable Track coverage must avoid the grant inventory")

    monkeypatch.setattr(search_service, "direct_entries", forbidden)
    result = await search(user, workspace, app, vector_driver)
    assert result[0], result


async def test_workspace_scope_ignores_foreign_track_with_matching_scalar_cache(
    vector_driver, monkeypatch
):
    from app.schemas.policy import Subject
    from app.schemas.retrieve import RetrieveRequest
    from app.services.agent_scope import current_scope_workspace_id
    from app.services.retrieval import search_service
    from tests.test_resource_location import graph

    user, workspace, app, track, entry = await indexed_graph(vector_driver)
    _, _, _, foreign_track, _ = await graph()
    foreign_track.workspace_id = workspace.id
    await foreign_track.save()

    async def forbidden(*args, **kwargs):
        raise AssertionError("Full rooted coverage does not need a grant inventory")

    monkeypatch.setattr(search_service, "direct_entries", forbidden)
    token = current_scope_workspace_id.set(workspace.id)
    try:
        found, *_ = await search_service.retrieve_passages(
            Subject(kind="human", id=user.id),
            RetrieveRequest(
                query="collection point", scope=f"workspace:{workspace.id}"
            ),
            driver=vector_driver,
            k=40,
            top_n=5,
        )
    finally:
        current_scope_workspace_id.reset(token)
    assert found and found[0].entry_id == entry.id


async def test_source_backed_entry_share_is_searchable_and_revocation_is_live(
    vector_driver,
):
    from app.middleware.permissions_cache import isolated_permissions_cache
    from app.services.share_link_lifecycle import (
        redeem_source_link,
        revoke_source_grant,
    )
    from app.services.share_links import mint_share_link
    from tests.test_access_lifecycle import verified_subject

    owner, workspace, app, track, entry = await indexed_graph(vector_driver)
    recipient = await verified_subject(workspace, "guest")
    minted = await mint_share_link(owner.id, "entry", entry.id, role="viewer")
    await redeem_source_link(recipient.id, minted["token"])
    with isolated_permissions_cache():
        found, *_ = await search(recipient, workspace, app, vector_driver)
        assert found and found[0].entry_id == entry.id
    await revoke_source_grant(
        actor_id=owner.id,
        link_id=minted["share_link"]["id"],
        recipient_id=recipient.id,
    )
    with isolated_permissions_cache():
        assert (await search(recipient, workspace, app, vector_driver))[0] == []


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


async def test_repeated_titles_do_not_hide_the_answer_in_broad_search(
    vector_driver, monkeypatch
):
    """Exercise the actual encoder, passage ranks and canonical source gate."""
    from app.middleware.permissions_cache import isolated_permissions_cache
    from app.models.edges import COLLABORATES_ON, CONTAINS, EXCLUDED_FROM
    from app.models.nodes import Entry
    from app.services.retrieval import passage_ranking
    from tests.test_entry_projection_authority import reader_in
    from tests.test_resource_location import graph

    user, workspace, app, track, answer = await graph()
    answer.title = "Safety assembly — Anna Regina #064"
    answer.body = (
        "If the fire alarm sounds, meet in the eastern courtyard beside the blue "
        "water tank. Supervisors account for all visitors.\n\n"
        "Operational record for the Anna Regina service team. Reference HS-01-0064. "
        "Reviewed in October 2026. The assigned coordinator checks completion "
        "and records outstanding actions."
    )
    await answer.save()
    await indexing.enqueue_entry(answer)
    for i in range(20):
        entry = await Entry.create(
            title=f"Rainy season dispatch — Georgetown #{i:03}",
            track_id=track.id,
            body="During heavy rainfall confirm river conditions before dispatch.",
        )
        await track.connect(entry, edge=CONTAINS)
        await indexing.enqueue_entry(entry)
    assert (await indexing.deliver_index_jobs(limit=30))["indexed"] == 21
    query = "Where do people gather during an evacuation?"
    with monkeypatch.context() as old:
        old.setattr(passage_ranking, "diverse_passage_ranks", lambda rows, **_: rows)
        with isolated_permissions_cache():
            baseline = (
                await search(
                    user, workspace, app, vector_driver, query, mode="semantic"
                )
            )[0]
    assert answer.id not in [row.entry_id for row in baseline]
    with isolated_permissions_cache():
        updated = (
            await search(user, workspace, app, vector_driver, query, mode="semantic")
        )[0]
    found = next(row for row in updated if row.entry_id == answer.id)
    assert "blue water tank" in " ".join(p.text for p in found.passages)
    assert found.action_url.endswith("?entry=" + answer.id)
    reader = await reader_in(workspace)
    await reader.connect(track, edge=COLLABORATES_ON, role="viewer")
    await reader.connect(answer, edge=EXCLUDED_FROM)
    with isolated_permissions_cache():
        denied = (
            await search(reader, workspace, app, vector_driver, query, mode="semantic")
        )[0]
    assert answer.id not in [row.entry_id for row in denied]


@pytest.mark.parametrize("include_body", [True, False])
async def test_title_only_hit_has_bounded_selected_current_support(
    vector_driver, monkeypatch, include_body
):
    from app.services.retrieval.passage_store import PassageStore
    from tests.test_resource_location import graph

    user, workspace, app, track, entry = await graph()
    entry.title = "Emergency assembly handbook"
    entry.body = "Meet in the southern courtyard beside the blue water tank. Supervisors account for visitors."
    await entry.save()
    track.search_index = {"fields": ["title", "body"] if include_body else ["title"]}
    await track.save()
    await indexing.enqueue_entry(entry)
    assert (await indexing.deliver_index_jobs())["indexed"] == 1
    original = PassageStore.search

    async def titles_only(self, *args, **kwargs):
        rows = await original(self, *args, **kwargs)
        return [row for row in rows if row["field_path"] == "title"]

    monkeypatch.setattr(PassageStore, "search", titles_only)
    found = (
        await search(
            user,
            workspace,
            app,
            vector_driver,
            "Emergency assembly handbook",
            mode="semantic",
            context_tokens=64,
        )
    )[0]
    assert len(found) == 1
    assert sum(p.tokens for p in found[0].passages) <= 64
    support = [p for p in found[0].passages if p.context_only]
    assert bool(support) == include_body
    if include_body:
        assert "southern courtyard" in support[0].text
        assert support[0].text == entry.body[support[0].start : support[0].end]
        assert "southern courtyard" in found[0].snippet
    else:
        assert all(p.field == "title" for p in found[0].passages)
    entry.body = "Changed canonical instructions that are not yet indexed."
    await entry.save()
    after = (
        await search(
            user,
            workspace,
            app,
            vector_driver,
            "Emergency assembly handbook",
            mode="semantic",
        )
    )[0]
    if include_body:
        assert after == []
    else:
        assert after and all(p.field == "title" for p in after[0].passages)
