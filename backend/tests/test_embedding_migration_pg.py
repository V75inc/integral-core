"""Real derived storage and rooted graph tests for model migration cutover."""

import json

import httpx
import pytest
from jvspatial.core.context import get_default_context

from app.api.errors import ResourceConflictError
from app.models.nodes import Entry
from app.services.retrieval import embedding_configuration as config
from app.services.retrieval import indexing, workspace_reindex
from app.services.retrieval.index_definition import embedding_profile
from app.services.retrieval.passage_store import passage_store
from app.services.walkers.workspace_index_page import WorkspaceIndexPageWalker
from tests.test_embedding_configuration import fixture, keys, mock_embedding_http
from tests.test_semantic_indexing_pg import vector_driver

pytestmark = [pytest.mark.integration, pytest.mark.slow, pytest.mark.asyncio]


async def prepare(driver, monkeypatch):
    user, workspace, connection, body = await fixture()
    entries, _ = await WorkspaceIndexPageWalker(workspace_id=workspace.id).resume()
    assert len(entries) == 1
    entry = entries[0]
    entry.body = "The fire assembly point is the eastern courtyard."
    await entry.save()
    await indexing.enqueue_entry(entry)
    assert (await indexing.deliver_index_jobs())["indexed"] == 1
    calls = []

    def respond(request):
        texts = json.loads(request.content)["input"]
        calls.extend(texts)
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": i, "embedding": [0.5] * 16} for i in range(len(texts))
                ]
            },
        )

    mock_embedding_http(monkeypatch, httpx.MockTransport(respond))
    return user, workspace, entry, body, calls


async def request_row(workspace_id):
    rows = await get_default_context().database.find(
        "object",
        {"entity": config.REBUILD, "context.workspace_id": workspace_id},
        sort=[("context.revision", -1)],
        limit=1,
    )
    return rows[0]


async def finish_rebuild(workspace_id):
    for _ in range(20):
        row = await request_row(workspace_id)
        if row["context"]["status"] == "ready":
            return
        await get_default_context().database.find_one_and_update(
            "object", {"id": row["id"]}, {"$set": {"context.due": 0}}
        )
        await workspace_reindex.process_workspace_rebuilds()
    raise AssertionError("Rebuild did not reach verified readiness")


async def test_serving_stays_pinned_until_real_manifest_verification(
    vector_driver, monkeypatch
):
    user, workspace, entry, body, calls = await prepare(vector_driver, monkeypatch)
    old = embedding_profile()
    saved = await config.save(user.user_id, workspace.id, body)
    assert saved["profile_id"] == old.id and saved["pending_profile_id"] != old.id
    await workspace_reindex.process_workspace_rebuilds()
    assert (await config.resolve_workspace_embedding_profile(workspace.id)).id == old.id
    pending = await config.job_profile(workspace.id, saved["pending_profile_id"])
    assert (await indexing.deliver_index_jobs())["indexed"] == 1
    assert calls
    await workspace_reindex.process_workspace_rebuilds()
    row = await request_row(workspace.id)
    assert row["context"]["status"] == "ready"
    assert row["context"]["verified"] == 1
    assert (
        await config.resolve_workspace_embedding_profile(workspace.id)
    ).id == pending.id
    assert await passage_store(vector_driver, pending).integrity(
        entry.id, (await indexing.index_document(entry))["source_hash"]
    )


async def test_corrupt_pending_projection_is_repaired_before_activation(
    vector_driver, monkeypatch
):
    user, workspace, entry, body, _ = await prepare(vector_driver, monkeypatch)
    saved = await config.save(user.user_id, workspace.id, body)
    await workspace_reindex.process_workspace_rebuilds()
    await indexing.deliver_index_jobs()
    pending = await config.job_profile(workspace.id, saved["pending_profile_id"])
    store = passage_store(vector_driver, pending)
    async with (await store.ensure_schema()).acquire() as conn:
        await conn.execute(
            f"DELETE FROM {store.table} WHERE entry_id=$1 AND profile_id=$2 AND ordinal=0",
            entry.id,
            pending.id,
        )
    await workspace_reindex.process_workspace_rebuilds()
    assert (
        await config.resolve_workspace_embedding_profile(workspace.id)
    ).id == embedding_profile().id
    assert (await indexing.deliver_index_jobs())["indexed"] == 1
    row = await request_row(workspace.id)
    await get_default_context().database.find_one_and_update(
        "object", {"id": row["id"]}, {"$set": {"context.due": 0}}
    )
    await finish_rebuild(workspace.id)
    assert (
        await config.resolve_workspace_embedding_profile(workspace.id)
    ).id == pending.id


async def test_committed_edit_at_activation_boundary_forces_reverification(
    vector_driver, monkeypatch
):
    user, workspace, entry, body, _ = await prepare(vector_driver, monkeypatch)
    saved = await config.save(user.user_id, workspace.id, body)
    await workspace_reindex.process_workspace_rebuilds()
    await indexing.deliver_index_jobs()
    original = workspace_reindex._activate

    async def edit_then_activate(row, fence, profile):
        from app.services.entry_write_scope import entry_write_scope

        async with entry_write_scope(entry.id):
            fresh = await Entry.find_one({"id": entry.id})
            fresh.body = "The assembly point has moved to the north depot."
            await fresh.save()
            await indexing.enqueue_entry(fresh)
        await original(row, fence, profile)

    monkeypatch.setattr(workspace_reindex, "_activate", edit_then_activate)
    await workspace_reindex.process_workspace_rebuilds()
    assert (
        await config.resolve_workspace_embedding_profile(workspace.id)
    ).id == embedding_profile().id
    assert (await request_row(workspace.id))["context"]["status"] == "pending"
    monkeypatch.setattr(workspace_reindex, "_activate", original)
    assert (await indexing.deliver_index_jobs())["indexed"] == 2
    row = await request_row(workspace.id)
    await get_default_context().database.find_one_and_update(
        "object", {"id": row["id"]}, {"$set": {"context.due": 0}}
    )
    await finish_rebuild(workspace.id)
    assert (await config.resolve_workspace_embedding_profile(workspace.id)).id == saved[
        "pending_profile_id"
    ]


async def test_cancel_fences_old_pending_work(vector_driver, monkeypatch):
    user, workspace, entry, body, calls = await prepare(vector_driver, monkeypatch)
    saved = await config.save(user.user_id, workspace.id, body)
    await workspace_reindex.process_workspace_rebuilds()
    cancelled = await config.cancel(user.user_id, workspace.id, saved["revision"])
    assert cancelled["rebuild_status"] == "cancelled"
    assert not cancelled["pending_model"]
    assert (await request_row(workspace.id))["context"]["status"] == "cancelled"
    with pytest.raises(ResourceConflictError):
        await config.cancel(user.user_id, workspace.id, saved["revision"])
    assert await config.job_profile(workspace.id, saved["pending_profile_id"]) is None
    stats = await indexing.deliver_index_jobs()
    assert stats["superseded"] == 1 and calls == []
    assert (
        await config.resolve_workspace_embedding_profile(workspace.id)
    ).id == embedding_profile().id


async def test_workspace_frontier_exceeds_one_thousand_without_scalar_cache_discovery():
    from app.models.edges import CONTAINS
    from tests.test_resource_location import graph

    _, workspace, _, track, first = await graph()
    expected = {first.id}
    for i in range(1005):
        entry = await Entry.create(
            title=f"Rebuild source {i}",
            track_id=track.id,
            workspace_id="stale-scalar-cache",
        )
        await track.connect(entry, edge=CONTAINS)
        expected.add(entry.id)
    _, other, _, _, foreign = await graph()
    found, frames = [], []
    for _ in range(40):
        page, frames = await WorkspaceIndexPageWalker(
            workspace_id=workspace.id, frames=frames, page_size=100
        ).resume()
        assert len(page) <= 100 and len(frames) <= 2
        found.extend(entry.id for entry in page)
        if not frames:
            break
    assert len(found) == len(set(found)) == 1006
    assert set(found) == expected and foreign.id not in found


async def test_revoked_serving_provider_still_allows_current_keyword_publication(
    vector_driver, monkeypatch
):
    from types import SimpleNamespace

    from app.services.retrieval.profile_encoder import (
        WorkspaceEmbeddingUnavailable,
        encode_profile,
    )
    from tests.test_semantic_indexing_pg import search

    user, workspace, entry, body, calls = await prepare(vector_driver, monkeypatch)
    saved = await config.save(user.user_id, workspace.id, body)
    await workspace_reindex.process_workspace_rebuilds()
    await indexing.deliver_index_jobs()
    await finish_rebuild(workspace.id)
    profile = await config.resolve_workspace_embedding_profile(workspace.id)
    connection = await config.owned_connection(user.user_id, body.connection_id)
    await connection.delete()
    before = len(calls)
    entry.body = "The emergency rendezvous is now the western orchard."
    await entry.save()
    await indexing.enqueue_entry(entry)
    assert (await indexing.deliver_index_jobs())["failed"] == 1
    document = await indexing.index_document(entry)
    found, *_ = await search(
        user,
        workspace,
        SimpleNamespace(id=document["app_id"]),
        vector_driver,
        query="western orchard",
        mode="graph",
    )
    assert found and found[0].entry_id == entry.id
    assert "western orchard" in found[0].snippet
    with pytest.raises(WorkspaceEmbeddingUnavailable):
        await encode_profile(["never sent"], profile)
    assert len(calls) == before
    assert not (await config.describe(user.user_id, workspace.id))["serving_available"]


async def test_cutover_closes_committed_tail_without_a_quiet_whole_corpus_window(
    vector_driver, monkeypatch
):
    user, workspace, entry, body, calls = await prepare(vector_driver, monkeypatch)
    saved = await config.save(user.user_id, workspace.id, body)
    await workspace_reindex.process_workspace_rebuilds()
    await indexing.deliver_index_jobs()
    activate = workspace_reindex._activate
    edits = 0

    async def keep_writing_before_activation(row, fence, profile):
        nonlocal edits
        from app.services.entry_write_scope import entry_write_scope

        async with entry_write_scope(entry.id):
            fresh = await Entry.find_one({"id": entry.id})
            fresh.body = "Latest committed maintenance instruction."
            fresh.record_revision += 1
            await fresh.save()
            await indexing.enqueue_entry(fresh)
        assert (await indexing.deliver_index_jobs())["indexed"] == 2
        edits += 1
        await activate(row, fence, profile)

    monkeypatch.setattr(workspace_reindex, "_activate", keep_writing_before_activation)
    await workspace_reindex.process_workspace_rebuilds()
    row = await request_row(workspace.id)
    assert edits == 1 and row["context"]["status"] == "ready"
    assert row["context"]["verified_epoch"] > row["context"]["verify_epoch"]
    assert (await config.resolve_workspace_embedding_profile(workspace.id)).id == saved[
        "pending_profile_id"
    ]


async def test_legacy_partial_verification_restarts_under_committed_journal(
    vector_driver, monkeypatch
):
    user, workspace, entry, body, _ = await prepare(vector_driver, monkeypatch)
    saved = await config.save(user.user_id, workspace.id, body)
    row = await request_row(workspace.id)
    database = get_default_context().database
    await database.find_one_and_update(
        "object",
        {"id": row["id"]},
        {
            "$set": {"context.phase": "catching_up", "context.verified": 999},
            "$unset": {"context.protocol_version": ""},
        },
    )
    await workspace_reindex.process_workspace_rebuilds()
    restarted = (await request_row(workspace.id))["context"]
    assert restarted["phase"] == "enumerating"
    assert restarted["protocol_version"] == 2
    assert restarted["verified"] == 0
    assert (await config.resolve_workspace_embedding_profile(workspace.id)).id != saved[
        "pending_profile_id"
    ]
    await workspace_reindex.process_workspace_rebuilds()
    await indexing.deliver_index_jobs()
    await finish_rebuild(workspace.id)
    assert (await request_row(workspace.id))["context"]["verified"] == 1


async def test_track_rebuild_during_verification_restarts_the_source_sweep(
    vector_driver, monkeypatch
):
    from app.services.retrieval.index_management import request_reindex

    user, workspace, entry, body, _ = await prepare(vector_driver, monkeypatch)
    await config.save(user.user_id, workspace.id, body)
    await workspace_reindex.process_workspace_rebuilds()
    await indexing.deliver_index_jobs()
    activate = workspace_reindex._activate

    async def invalidate_before_activation(row, fence, profile):
        await request_reindex(user.id, entry.track_id, workspace.id)
        await activate(row, fence, profile)

    monkeypatch.setattr(workspace_reindex, "_activate", invalidate_before_activation)
    await workspace_reindex.process_workspace_rebuilds()
    ctx = (await request_row(workspace.id))["context"]
    assert ctx["status"] == "pending" and ctx["phase"] == "verifying"
    assert ctx["verified"] == 0 and ctx["verify_scope_epoch"] > 0
    monkeypatch.setattr(workspace_reindex, "_activate", activate)
    await finish_rebuild(workspace.id)


async def test_graceful_worker_shutdown_releases_only_its_owned_rebuild(
    vector_driver, monkeypatch
):
    import asyncio

    user, workspace, entry, body, _ = await prepare(vector_driver, monkeypatch)
    await config.save(user.user_id, workspace.id, body)
    original = workspace_reindex._page

    async def cancelled(*args):
        raise asyncio.CancelledError()

    monkeypatch.setattr(workspace_reindex, "_page", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await workspace_reindex.process_workspace_rebuilds()
    assert (await request_row(workspace.id))["context"]["status"] == "pending"
    monkeypatch.setattr(workspace_reindex, "_page", original)
    await workspace_reindex.process_workspace_rebuilds()
    await indexing.deliver_index_jobs()
    await finish_rebuild(workspace.id)


async def test_expired_fractional_rebuild_lease_is_reclaimed_on_postgres(
    vector_driver, monkeypatch
):
    import time

    user, workspace, entry, body, _ = await prepare(vector_driver, monkeypatch)
    await config.save(user.user_id, workspace.id, body)
    row = await request_row(workspace.id)
    await get_default_context().database.find_one_and_update(
        "object",
        {"id": row["id"]},
        {
            "$set": {
                "context.status": "working",
                "context.lease": "dead-worker",
                "context.lease_until": time.time() - 1.1234567,
            }
        },
    )
    await workspace_reindex.process_workspace_rebuilds()
    recovered = (await request_row(workspace.id))["context"]
    assert recovered["status"] == "pending" and recovered["enumerated"] == 1
    assert recovered["lease"] != "dead-worker"
    await indexing.deliver_index_jobs()
    await finish_rebuild(workspace.id)


async def test_expired_fractional_delivery_and_track_leases_are_reclaimed(
    vector_driver, monkeypatch
):
    import time

    from app.services.retrieval.index_management import (
        process_reindex_requests,
        request_reindex,
    )

    user, workspace, entry, body, _ = await prepare(vector_driver, monkeypatch)
    await request_reindex(user.id, entry.track_id, workspace.id)
    database = get_default_context().database
    rows = await database.find("object", {"entity": "TrackIndexRequest"}, limit=2)
    assert len(rows) == 1
    expired = {
        "context.status": "working",
        "context.lease": "dead-worker",
        "context.lease_until": time.time() - 1.1234567,
    }
    await database.find_one_and_update(
        "object", {"id": rows[0]["id"]}, {"$set": expired}
    )
    await process_reindex_requests()
    assert (await database.get("object", rows[0]["id"]))["context"][
        "status"
    ] == "queued"
    jobs = await database.find(
        "object", {"entity": indexing.ENTITY, "context.entry_id": entry.id}, limit=2
    )
    assert len(jobs) == 1
    await database.find_one_and_update(
        "object", {"id": jobs[0]["id"]}, {"$set": expired}
    )
    assert (await indexing.deliver_index_jobs())["indexed"] == 1
