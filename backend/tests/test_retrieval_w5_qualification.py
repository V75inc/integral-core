"""Local W5 evidence: ranking rubric, interface matrix, and fixture load bounds.

UI mode wiring is covered by ``frontend/src/hooks/__tests__/useRetrieve.test.tsx``
and ``frontend/src/features/settings/searchMode.test.tsx``. Authorized agent
search is covered by ``test_delegated_app_search_uses_real_exact_function_authority``.
Profile isolation is covered by
``test_scoped_recall_profile_separation_and_guarded_publish``.
Fixture time and memory ceilings are runaway guards, not a hosted capacity claim.
"""

import asyncio
import time
import tracemalloc

import pytest

from app.models.edges import CONTAINS, HAS_ATTACHMENT
from app.models.nodes import Attachment, Entry
from app.services.agent_scope import current_scope_workspace_id
from app.services.retrieval import indexing
from app.services.retrieval.index_definition import digest, embedding_profile
from tests.test_resource_location import graph
from tests.test_semantic_indexing_pg import search, vector_driver

# The import registers the pgvector fixture. Tests request it by that name.
_REGISTERED_VECTOR_DRIVER = vector_driver

pytestmark = [pytest.mark.integration, pytest.mark.slow, pytest.mark.asyncio]

INDEX_BUDGET_S = 180
SEARCH_BUDGET_S = 20
MEMORY_BUDGET_BYTES = 512 * 1024 * 1024


async def _add(track, title, body, **custom):
    """Create a track entry with optional custom fields."""
    entry = await Entry.create(title=title, track_id=track.id, body=body)
    if custom:
        entry.custom_fields = custom
        await entry.save()
    await track.connect(entry, edge=CONTAINS)
    return entry


def _rank(found, entry_id):
    """Return the result position of an entry, or None when it is absent."""
    return next(
        (index for index, item in enumerate(found) if item.entry_id == entry_id),
        None,
    )


async def test_ranked_evidence_and_interface_matrix(vector_driver, monkeypatch):
    """Paraphrases, near misses, metadata, files, and the shared search surfaces."""
    from app.api import retrieve as retrieval_api
    from app.api.retrieve import _dispatch_retrieve_modes
    from app.middleware.permissions_cache import isolated_permissions_cache
    from app.models.edges import COLLABORATES_ON, EXCLUDED_FROM, IS_MEMBER_OF
    from app.models.nodes import User
    from app.schemas.policy import Subject
    from app.schemas.retrieve import RetrieveRequest
    from app.services.app_graph import catalog_user
    from app.services.hooks.registry import ToolContext

    user, workspace, app, track, target = await graph()
    track.search_index = {
        "fields": ["title", "body", "custom_fields.case_code"],
        "metadata_fields": ["custom_fields.case_code"],
        "attachments": "content",
    }
    await track.save()
    target.title = "Battery handling"
    target.body = (
        "Damaged lithium batteries must go to the council recycling point. "
        "The lot marker is battery-lot-77QX."
    )
    target.custom_fields = {"case_code": "REC-8841"}
    await target.save()
    similar = await _add(
        track,
        "Battery stock",
        "Sealed lithium battery packs stay on warehouse shelf B. "
        "Do not send this new stock out for disposal.",
    )
    await _add(track, "Furniture", "Office chairs are stored in the east cupboard.")
    manual = await _add(track, "Safety manual", "Filed manual.")
    document = await Attachment.create(
        filename="assembly.pdf",
        mime_type="application/pdf",
        scan_status="clean",
        metadata_status="complete",
        extracted_text="The emergency assembly is beside the western gate.",
    )
    await manual.connect(document, edge=HAS_ATTACHMENT)
    picture = await _add(track, "Site picture", "Posted picture.")
    image = await Attachment.create(
        filename="chute.png",
        mime_type="image/png",
        scan_status="clean",
        metadata_status="complete",
        content_hash="chute-v1",
        metadata={
            "semantic": {
                "source_hash": "chute-v1",
                "description": "A blue site diagram of the north loading chute.",
            }
        },
    )
    await picture.connect(image, edge=HAS_ATTACHMENT)
    for entry in (target, similar, manual, picture):
        await indexing.enqueue_entry(entry)
    assert (await indexing.deliver_index_jobs())["indexed"] == 4

    ranked = [
        ("battery-lot-77QX", ("lexical", "hybrid", "semantic"), target, similar, {}),
        (
            "Where do we dispose of broken lithium cells?",
            ("hybrid", "semantic"),
            target,
            similar,
            {},
        ),
        (
            "Show me the council site that takes damaged cells.",
            ("hybrid", "semantic"),
            target,
            similar,
            {},
        ),
        ("REC-8841", ("lexical", "hybrid", "semantic"), target, similar, {}),
        (
            "western gate",
            ("lexical", "hybrid"),
            manual,
            target,
            {"attachment_kind": "document"},
        ),
        (
            "blue site diagram",
            ("hybrid", "semantic"),
            picture,
            target,
            {"attachment_kind": "image"},
        ),
    ]
    for query, modes, expected, rejected, options in ranked:
        for mode in modes:
            found, *_ = await search(
                user, workspace, app, vector_driver, query=query, mode=mode, **options
            )
            assert found and found[0].entry_id == expected.id, (query, mode)
            assert found[0].action_url and found[0].source_revision
            assert _rank(found, rejected.id) != 0

    monkeypatch.setattr(retrieval_api, "get_embedding_store", lambda: vector_driver)
    token = current_scope_workspace_id.set(workspace.id)
    try:
        for mode in ("lexical", "hybrid", "semantic"):
            effective = "lexical" if mode == "lexical" else mode
            found, *_ = await _dispatch_retrieve_modes(
                Subject(kind="human", id=user.id),
                RetrieveRequest(
                    query="battery-lot-77QX", scope=f"app:{app.id}", mode=mode
                ),
                requested_mode=mode,
                effective_mode=effective,
                degraded=False,
                k=150,
                top_n=5,
            )
            assert found and found[0].entry_id == target.id, mode
        context = ToolContext(
            user_id=user.id, workspace_id=workspace.id, scope=f"app:{app.id}"
        )
        context.app_id = app.id
        for mode in ("lexical", "hybrid", "semantic"):
            result = await context.search_entries(
                query="battery-lot-77QX",
                track_type=track.id,
                mode=mode,
                eligible_entry_ids=[target.id],
            )
            assert result["available"], result
            assert result["results"][0]["entry_id"] == target.id
            assert result["results"][0]["action_url"]
        from app.agentive.agent_actions.retrieve_context import retrieve_context

        # An agent id with no grant must not receive the labelled source.
        # Authorized agent search is test_delegated_app_search_uses_real_exact_function_authority.
        for mode in ("lexical", "hybrid", "semantic"):
            response = await retrieve_context(
                agent_id=user.id,
                query="battery-lot-77QX",
                scope=f"app:{app.id}",
                mode=mode,
            )
            assert response.results == [], mode
            assert response.candidates_examined == 0
    finally:
        current_scope_workspace_id.reset(token)

    target.body = "The collection point has moved to the north depot."
    await target.save()
    stale, *_ = await search(
        user, workspace, app, vector_driver, query="battery-lot-77QX"
    )
    assert all(item.entry_id != target.id for item in stale)
    await indexing.enqueue_entry(target)
    assert (await indexing.deliver_index_jobs())["indexed"] == 1
    moved, *_ = await search(
        user, workspace, app, vector_driver, query="Where is the collection point?"
    )
    assert moved and moved[0].entry_id == target.id
    await target.delete()
    deleted, *_ = await search(user, workspace, app, vector_driver, query="north depot")
    assert all(item.entry_id != target.id for item in deleted)

    reader = await User.create(display_name="Matrix reader")
    await catalog_user(reader)
    await reader.connect(workspace, edge=IS_MEMBER_OF, role="member")
    await reader.connect(app, edge=COLLABORATES_ON, role="viewer")
    deny = await reader.connect(similar, edge=EXCLUDED_FROM)
    with isolated_permissions_cache():
        hidden, *_ = await search(
            reader, workspace, app, vector_driver, query="warehouse shelf"
        )
        assert all(item.entry_id != similar.id for item in hidden)
    await deny.delete()


async def test_cancellation_restart_and_provider_isolation(vector_driver, monkeypatch):
    """Cancellation keeps the lease, expiry retries, and one provider failure stays local."""
    from app.services.retrieval import profile_encoder

    user, workspace, app, _track, entry = await graph()
    entry.body = "A complete searchable paragraph about pump maintenance."
    await entry.save()
    await indexing.enqueue_entry(entry)
    original = profile_encoder.encode_profile

    async def cancel(texts, profile, **kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(profile_encoder, "encode_profile", cancel)
    with pytest.raises(asyncio.CancelledError):
        await indexing.deliver_index_jobs()
    database = (await entry.get_context()).database
    identity = "o.EntryIndexJob." + digest([entry.id, embedding_profile().id])
    row = await database.get("object", identity)
    assert row["context"]["status"] == "working"
    await database.find_one_and_update(
        "object",
        {"id": identity},
        {"$set": {"context.lease_until": 0}},
    )
    monkeypatch.setattr(profile_encoder, "encode_profile", original)
    _other_user, _other_workspace, _other_app, _other_track, other = await graph()
    other.body = "FAILSPACE valve inspection stays on its own job."
    await other.save()
    await indexing.enqueue_entry(other)

    async def fail_one(texts, profile, **kwargs):
        if any("FAILSPACE" in text for text in texts):
            raise RuntimeError("provider body must not be stored")
        return await original(texts, profile)

    monkeypatch.setattr(profile_encoder, "encode_profile", fail_one)
    stats = await indexing.deliver_index_jobs()
    assert stats["indexed"] >= 1
    assert stats["failed"] >= 1
    failed = await database.get(
        "object", "o.EntryIndexJob." + digest([other.id, embedding_profile().id])
    )
    assert failed["context"]["error"] == "RuntimeError"
    found, *_ = await search(
        user, workspace, app, vector_driver, query="pump maintenance"
    )
    assert found and found[0].entry_id == entry.id


async def test_local_index_load_stays_inside_fixture_bounds(vector_driver, monkeypatch):
    """A search during indexing returns the published record inside the latency budget."""
    records = []
    for index in range(3):
        user, workspace, app, _track, entry = await graph()
        entry.title = f"Workspace {index}"
        entry.body = "Workspace note."
        await entry.save()
        attachment = await Attachment.create(
            filename=f"log-{index}.txt",
            mime_type="text/plain",
            scan_status="clean",
            metadata_status="complete",
            extracted_text=f"Unique workspace marker WS-{index}-MARKER.",
        )
        await entry.connect(attachment, edge=HAS_ATTACHMENT)
        records.append((user, workspace, app, entry, index))

    await indexing.enqueue_entry(records[0][3])
    assert (await indexing.deliver_index_jobs())["indexed"] == 1
    for _user, _workspace, _app, entry, _index in records[1:]:
        await indexing.enqueue_entry(entry)

    import threading

    from app.services.retrieval import encoding_runtime

    loop = asyncio.get_running_loop()
    admission = getattr(loop, "_integral_embedding_admission", None)
    if admission is not None:
        admission.query_wait_s = 0.0
        admission.background_wait_s = 0.0
        admission.peak_waiting = 0
    background_holds = threading.Event()
    original_run = encoding_runtime.run_encoding

    async def paced(operation):
        def wrapped():
            if not encoding_runtime.is_interactive() and not background_holds.is_set():
                background_holds.set()
                time.sleep(0.15)
            return operation()

        return await original_run(wrapped)

    monkeypatch.setattr(encoding_runtime, "run_encoding", paced)

    async def contended_search():
        query_started = time.perf_counter()
        found, *_ = await search(
            records[0][0],
            records[0][1],
            records[0][2],
            vector_driver,
            query="WS-0-MARKER",
            mode="hybrid",
        )
        return time.perf_counter() - query_started, found

    tracemalloc.start()
    started = time.perf_counter()
    indexing_task = asyncio.create_task(indexing.deliver_index_jobs())
    assert await asyncio.to_thread(background_holds.wait, 5)
    search_latency, during = await contended_search()
    stats = await indexing_task
    elapsed = time.perf_counter() - started
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    admission = getattr(loop, "_integral_embedding_admission", None)
    assert during and during[0].entry_id == records[0][3].id
    assert stats["indexed"] == 2, stats
    assert elapsed < INDEX_BUDGET_S, elapsed
    assert search_latency < SEARCH_BUDGET_S, search_latency
    assert peak < MEMORY_BUDGET_BYTES, peak
    assert admission is not None
    assert admission.peak_waiting >= 1
    assert 0 < admission.query_wait_s < SEARCH_BUDGET_S, admission.query_wait_s
    assert admission.background_wait_s < INDEX_BUDGET_S, admission.background_wait_s
    assert admission.waiting == 0
    assert admission.running is False

    for user, workspace, app, entry, index in records:
        query_started = time.perf_counter()
        found, *_ = await search(
            user,
            workspace,
            app,
            vector_driver,
            query=f"WS-{index}-MARKER",
            mode="lexical",
        )
        assert time.perf_counter() - query_started < SEARCH_BUDGET_S
        assert found and found[0].entry_id == entry.id


async def test_interactive_search_is_rejected_when_the_encoder_stays_busy(
    vector_driver, monkeypatch
):
    """Overload rejects the search instead of returning an empty success."""
    import threading

    from app.services.retrieval import encoding_runtime
    from app.services.retrieval.embedding_store import EmbeddingBusy

    _user, _workspace, _app, _track, entry = await graph()
    entry.body = "Overload marker OVERLOAD-MARKER."
    await entry.save()
    await indexing.enqueue_entry(entry)
    monkeypatch.setenv("EMBEDDING_QUEUE_TIMEOUT_MS", "200")
    background_holds = threading.Event()
    release_background = threading.Event()
    original_run = encoding_runtime.run_encoding

    async def paced(operation):
        def wrapped():
            if not encoding_runtime.is_interactive():
                background_holds.set()
                release_background.wait(5)
            return operation()

        return await original_run(wrapped)

    monkeypatch.setattr(encoding_runtime, "run_encoding", paced)
    indexing_task = asyncio.create_task(indexing.deliver_index_jobs())
    assert await asyncio.to_thread(background_holds.wait, 5)
    with pytest.raises(EmbeddingBusy):
        await search(
            _user,
            _workspace,
            _app,
            vector_driver,
            query="OVERLOAD-MARKER",
            mode="hybrid",
        )
    release_background.set()
    assert (await indexing_task)["indexed"] == 1
