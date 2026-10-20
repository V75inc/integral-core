"""Real pgvector + actual encoder through durable jobs and authorized retrieval.

Opt in with SEMANTIC_TEST_DSN. Every test owns and drops unique vector tables;
canonical graph fixtures use the ordinary isolated test database.
"""

import asyncio
import os
import time
import uuid
from dataclasses import replace

import pytest
from jvspatial.core.context import get_default_context

from app.models.edges import EXCLUDED_FROM
from app.models.nodes import Track
from app.schemas.policy import Subject
from app.schemas.retrieve import RetrieveRequest
from app.services.agent_scope import current_scope_workspace_id
from app.services.retrieval import indexing
from app.services.retrieval.index_definition import digest, embedding_profile
from app.services.retrieval.passage_store import PassageStore, passage_store
from app.services.retrieval.pgvector_driver import PgVectorEmbeddingStore
from app.services.retrieval.search_service import retrieve_passages
from tests.test_resource_location import graph

pytestmark = [pytest.mark.integration, pytest.mark.slow, pytest.mark.asyncio]


@pytest.fixture
async def vector_driver(monkeypatch):
    dsn = os.getenv("SEMANTIC_TEST_DSN")
    if not dsn:
        pytest.skip("SEMANTIC_TEST_DSN is required for actual pgvector E2E")
    driver = PgVectorEmbeddingStore(
        dsn=dsn, table="semantic_test_" + uuid.uuid4().hex[:12]
    )
    from app.services import retrieval

    monkeypatch.setattr(retrieval, "get_embedding_store", lambda: driver)
    try:
        yield driver
    finally:
        pool = await driver._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(f"DROP TABLE IF EXISTS {driver._table}_passage")
            await conn.execute(f"DROP TABLE IF EXISTS {driver._table}")
        await driver.close()


async def search(
    user, workspace, app, driver, query="How do we recycle damaged batteries?", **kwargs
):
    token = current_scope_workspace_id.set(workspace.id)
    try:
        return await retrieve_passages(
            Subject(kind="human", id=user.id),
            RetrieveRequest(query=query, scope=f"app:{app.id}", **kwargs),
            driver=driver,
            k=150,
            top_n=5,
        )
    finally:
        current_scope_workspace_id.reset(token)


async def indexed_graph(driver):
    user, workspace, app, track, entry = await graph()
    entry.title = "Equipment safety"
    entry.body = (
        "Background notes. " * 400
    ) + " Damaged lithium batteries must go to the council recycling point."
    await entry.save()
    await indexing.enqueue_entry(entry)
    stats = await indexing.deliver_index_jobs()
    assert stats == {"indexed": 1, "failed": 0, "superseded": 0}, stats
    return user, workspace, app, track, entry


async def test_real_index_and_retrieve_tail_budget_freshness_and_deletion(
    vector_driver,
):
    user, workspace, app, track, entry = await indexed_graph(vector_driver)
    found, *_ = await search(user, workspace, app, vector_driver, context_tokens=64)
    assert found and found[0].entry_id == entry.id
    assert any("council recycling" in p.text for p in found[0].passages)
    assert sum(p.tokens for result in found for p in result.passages) <= 64
    assert found[0].action_url and found[0].source_revision
    # An edit invalidates the old vector before any delivery takes place.
    entry.body = "The collection point has moved to the north depot."
    await entry.save()
    found, *_ = await search(user, workspace, app, vector_driver)
    assert found == []
    await indexing.enqueue_entry(entry)
    assert (await indexing.deliver_index_jobs())["indexed"] == 1
    found, *_ = await search(
        user, workspace, app, vector_driver, query="Where is the collection point?"
    )
    assert "north depot" in found[0].snippet
    await entry.delete()
    found, *_ = await search(user, workspace, app, vector_driver)
    assert found == []


async def test_current_exclusion_foreign_scope_and_lifecycle(vector_driver):
    user, workspace, app, track, entry = await indexed_graph(vector_driver)
    from app.models.edges import COLLABORATES_ON, IS_MEMBER_OF
    from app.models.nodes import User
    from app.services.app_graph import catalog_user

    reader = await User.create(display_name="Scoped reader")
    await catalog_user(reader)
    await reader.connect(workspace, edge=IS_MEMBER_OF, role="member")
    await reader.connect(app, edge=COLLABORATES_ON, role="viewer")
    deny = await reader.connect(entry, edge=EXCLUDED_FROM)
    from app.middleware.permissions_cache import isolated_permissions_cache

    with isolated_permissions_cache():
        assert (await search(reader, workspace, app, vector_driver))[0] == []
    await deny.delete()
    app.lifecycle_state = "uninstalled"
    await app.save()
    from app.api.errors import InsufficientPermissionsError

    with pytest.raises(InsufficientPermissionsError):
        await search(user, workspace, app, vector_driver)


async def test_retry_lease_and_superseded_worker(vector_driver, monkeypatch):
    user, workspace, app, track, entry = await graph()
    entry.body = "A complete searchable paragraph."
    await entry.save()
    await indexing.enqueue_entry(entry)
    from app.services.retrieval import embedding_model

    original = embedding_model.encode_passages

    async def unavailable(*args, **kwargs):
        raise RuntimeError("provider credential must never appear in persisted status")

    monkeypatch.setattr(embedding_model, "encode_passages", unavailable)
    assert (await indexing.deliver_index_jobs())["failed"] == 1
    database = get_default_context().database
    identity = "o.EntryIndexJob." + digest([entry.id, embedding_profile().id])
    row = await database.get("object", identity)
    assert row["context"]["error"] == "RuntimeError"
    await database.find_one_and_update(
        "object",
        {"id": identity},
        {
            "$set": {
                "context.status": "working",
                "context.lease": "lost-worker",
                "context.lease_until": 0,
            }
        },
    )
    monkeypatch.setattr(embedding_model, "encode_passages", original)
    assert (await indexing.deliver_index_jobs())["indexed"] == 1
    # A newer generation invalidates an encoder already working on the old one.
    await indexing.enqueue_entry(entry, force=True)

    async def supersede(texts, dimensions):
        vectors = await original(texts, dimensions)
        await indexing.enqueue_entry(entry, force=True)
        return vectors

    monkeypatch.setattr(embedding_model, "encode_passages", supersede)
    assert (await indexing.deliver_index_jobs())["superseded"] == 1
    monkeypatch.setattr(embedding_model, "encode_passages", original)
    assert (await indexing.deliver_index_jobs())["indexed"] == 1


async def test_scoped_recall_profile_separation_and_guarded_publish(vector_driver):
    profile = embedding_profile()
    store = passage_store(vector_driver, profile)
    vector = [1.0] + [0.0] * (profile.dimensions - 1)
    document = dict(
        entry_id="allowed",
        track_id="track",
        workspace_id="workspace",
        app_id="app",
        source_hash="hash",
        source_revision=1,
        metadata={},
    )
    passages = [dict(field="body", start=0, end=6, text="needle")]
    for i in range(20):
        await store.replace(
            {**document, "entry_id": f"foreign-{i}", "workspace_id": "foreign"},
            passages,
            [vector],
        )
    await store.replace(document, passages, [vector])
    result = await store.search(
        vector, workspace_id="workspace", track_ids=["track"], k=1
    )
    assert [r["entry_id"] for r in result] == ["allowed"]
    other = PassageStore(vector_driver, replace(profile, revision="another-space"))
    assert (
        await other.search(vector, workspace_id="workspace", track_ids=["track"], k=5)
        == []
    )

    async def invalid_lease():
        return False

    assert not await store.replace(
        {**document, "source_hash": "stale"}, passages, [vector], guard=invalid_lease
    )
    assert not await store.soft_delete("allowed", guard=invalid_lease)
    assert (
        await store.search(vector, workspace_id="workspace", track_ids=["track"], k=1)
    )[0]["source_hash"] == "hash"


async def test_reconcile_declaration_status_and_reindex_authority(vector_driver):
    user, workspace, app, track, entry = await graph()
    from app.api.errors import InsufficientPermissionsError
    from app.services.retrieval.index_management import (
        configure_index,
        index_status,
        process_reindex_requests,
        request_reindex,
    )

    await configure_index(user.id, track.id, {"fields": ["body"]}, workspace.id)
    from app.api.errors import ResourceConflictError

    with pytest.raises(ResourceConflictError):
        await configure_index(
            user.id, track.id, {"enabled": False}, workspace.id, expected_revision=0
        )
    assert (await Track.find_one({"id": track.id})).search_index_revision == 1
    entry.body = "A new searchable passage."
    await entry.save()
    await process_reindex_requests()
    assert (await indexing.deliver_index_jobs())["indexed"] == 1
    status = await index_status(user.id, track.id, workspace.id)
    assert status["store_ready"] and status["coverage_complete"], status
    assert status["coverage"]["current"] == 1
    with pytest.raises(InsufficientPermissionsError):
        await request_reindex(user.id, track.id, "foreign")
    await configure_index(user.id, track.id, {"enabled": False}, workspace.id)
    # No stale content after declaration changes, even before background work.
    assert (await search(user, workspace, app, vector_driver))[0] == []
    await indexing.reconcile_page()
    assert (await indexing.deliver_index_jobs())["indexed"] == 1


async def test_http_write_index_retrieve_and_approved_management(
    vector_driver,
    authenticated_client,
    test_user,
    monkeypatch,
):
    from app.agentive.services.staging_apply import bless_and_execute
    from app.agentive.tooling.dispatch import dispatch_tool
    from app.api import retrieve as retrieve_api
    from tests.test_retrieve import _seed_owned_track

    monkeypatch.setattr(retrieve_api, "get_embedding_store", lambda: vector_driver)
    monkeypatch.setattr(retrieve_api, "semantic_retrieval_available", lambda: True)
    track = await _seed_owned_track(test_user)
    response = await authenticated_client.post(
        "/api/entries",
        json={
            "track_id": track.id,
            "title": "Stored guidance",
            "body": "New helpers attend an induction and shadow an experienced colleague.",
        },
    )
    assert response.status_code == 200, response.text
    entry_id = response.json()["entry"]["id"]
    assert (await indexing.deliver_index_jobs())["indexed"] == 1
    response = await authenticated_client.post(
        "/api/retrieve",
        json={
            "query": "How are newcomers introduced?",
            "scope": f"track:{track.id}",
            "mode": "semantic",
            "context_tokens": 100,
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["results"][0]["entry_id"] == entry_id
    assert data["index"]["passage_tokens"] <= 100
    assert data["results"][0]["passages"]
    response = await authenticated_client.put(
        f"/api/tracks/{track.id}/search-index", json={"unknown": "refuse"}
    )
    assert response.status_code == 400, response.text
    principal = test_user.user_id
    status = await dispatch_tool(
        "integral_get_index_status",
        {"track_id": track.id},
        principal_id=principal,
        scope=track.workspace_id,
    )
    assert not status.is_error, status
    result = await dispatch_tool(
        "integral_configure_search_index",
        {
            "track_id": track.id,
            "definition": {"fields": ["body"]},
        },
        principal_id=principal,
        scope=track.workspace_id,
    )
    assert not result.is_error, result
    fresh = await Track.find_one({"id": track.id})
    assert fresh.search_index == {}  # Proposal alone does not mutate selection.
    token = result.data.get("token") or result.data.get("staged_change", {}).get(
        "token"
    )
    assert token, result.data
    outcome = await bless_and_execute(user_id=principal, token=token)
    assert outcome.get("consumed"), outcome
    assert (await Track.find_one({"id": track.id})).search_index["fields"] == ["body"]


async def test_direct_entry_grant_without_track_read_and_other_workspace(vector_driver):
    user, workspace, app, track, entry = await indexed_graph(vector_driver)
    from app.middleware.permissions_cache import isolated_permissions_cache
    from app.models.edges import COLLABORATES_ON, IS_MEMBER_OF
    from app.models.nodes import User
    from app.services.app_graph import catalog_user
    from app.services.permissions import can_view_entry, can_view_track

    reader = await User.create(display_name="Entry-only reader")
    await catalog_user(reader)
    await reader.connect(workspace, edge=IS_MEMBER_OF, role="member")
    grant = await reader.connect(entry, edge=COLLABORATES_ON, role="viewer")
    with isolated_permissions_cache():
        assert not await can_view_track(reader.id, track.id)
        assert await can_view_entry(reader.id, entry.id)
        found = (await search(reader, workspace, app, vector_driver))[0]
        assert [r.entry_id for r in found] == [entry.id]
    await grant.delete()
    with isolated_permissions_cache():
        assert (await search(reader, workspace, app, vector_driver))[0] == []
    other_user, other_workspace, other_app, _, _ = await graph()
    # Even a legitimate grant in both workspaces cannot override host binding.
    from app.api.errors import InsufficientPermissionsError

    token = current_scope_workspace_id.set(other_workspace.id)
    try:
        with pytest.raises(InsufficientPermissionsError):
            await retrieve_passages(
                Subject(kind="human", id=user.id),
                RetrieveRequest(query="safety", scope=f"workspace:{workspace.id}"),
                driver=vector_driver,
                k=150,
                top_n=5,
            )
    finally:
        current_scope_workspace_id.reset(token)


async def test_governed_app_boundary_is_shared_by_rest_and_agent_response(
    vector_driver, monkeypatch
):
    user, workspace, app, track, entry = await indexed_graph(vector_driver)
    from app.api import retrieve as retrieve_api

    results, dropped, examined, mode, degraded = await search(
        user, workspace, app, vector_driver
    )
    assert results
    app.installed_package_slug = "reference-protected"
    await app.save()
    monkeypatch.setattr(retrieve_api, "get_embedding_store", lambda: vector_driver)
    response = await retrieve_api.finalize_retrieval(
        RetrieveRequest(query="safety", scope=f"app:{app.id}"),
        results,
        dropped=dropped,
        examined=examined,
        effective_mode=mode,
        requested_mode=mode,
        degraded=degraded,
    )
    assert response.results == []
    assert response.boundary["declared_query_required"]
    assert response.dropped_for_boundary == 1


async def test_type_declaration_narrows_track_fields_without_dropping_explicit_fields(
    vector_driver,
):
    user, workspace, app, track, entry = await graph()
    from app.models.edges import CONTAINS, IS_OF_TYPE
    from app.models.nodes import EntryType

    entry_type = await EntryType.create(
        name="Note",
        track_id=track.id,
        form_schema={"search_index": {"exclude_fields": ["body"]}},
    )
    await track.connect(entry_type, edge=CONTAINS)
    await entry.connect(entry_type, edge=IS_OF_TYPE)
    entry.type_id = entry_type.id
    entry.custom_fields = {"summary": "Selected", "private": "Excluded"}
    await entry.save()
    track.search_index = {"fields": ["body", "custom_fields.summary"]}
    await track.save()
    document = await indexing.index_document(entry)
    assert document["texts"] == {"custom_fields.summary": "Selected"}
    first_hash = document["source_hash"]
    entry.record_revision += 1
    entry.tags = ["a-new-tag"]
    await entry.save()
    assert (await indexing.index_document(entry))["source_hash"] == first_hash


async def test_poison_source_does_not_block_other_records_and_recovers(
    vector_driver, monkeypatch
):
    user, workspace, app, track, first = await graph()
    from tests.test_retrieve import _seed_entry

    second = await _seed_entry(
        track, title="Healthy source", body="A searchable paragraph."
    )
    original = indexing.index_document

    async def damaged(entry, **kwargs):
        if entry.id == first.id:
            raise ValueError("invalid declaration")
        return await original(entry, **kwargs)

    monkeypatch.setattr(indexing, "index_document", damaged)
    assert await indexing.reconcile_page() == 2
    stats = await indexing.deliver_index_jobs()
    assert stats["indexed"] == 1 and stats["failed"] == 1, stats
    monkeypatch.setattr(indexing, "index_document", original)
    await indexing.reconcile_page()
    assert (await indexing.deliver_index_jobs())["indexed"] == 1
    # Bad global model configuration retains intent; it cannot reject a write.
    monkeypatch.setenv("EMBEDDING_MODEL_DIMENSIONS", "broken")
    await indexing.enqueue_entry(second)
    identity = "o.EntryIndexJob." + digest([second.id, "unconfigured"])
    row = await get_default_context().database.get("object", identity)
    assert row["context"]["error"] == "InvalidEmbeddingProfile"


async def test_invalid_embedding_configuration_preserves_primary_writes(
    vector_driver, monkeypatch
):
    user, workspace, app, track, _ = await graph()
    from app.services.entry_create import create_entry_in_track
    from app.services.retrieval.index_management import index_status

    monkeypatch.setenv("EMBEDDING_MODEL_DIMENSIONS", "broken")
    created = await create_entry_in_track(
        track=track,
        user_id=user.id,
        title="Still saved",
        body="A record remains usable during a search configuration error.",
        workspace_id=workspace.id,
    )
    status = await index_status(user.id, track.id, workspace.id)
    assert not status["store_ready"] and status["error"] == "InvalidEmbeddingProfile"
    identity = "o.EntryIndexJob." + digest([created.id, "unconfigured"])
    assert (await get_default_context().database.get("object", identity))["context"][
        "status"
    ] == "pending"
    monkeypatch.setenv("EMBEDDING_MODEL_DIMENSIONS", "384")
    await indexing.reconcile_page()
    assert (await indexing.deliver_index_jobs())["indexed"] == 2
