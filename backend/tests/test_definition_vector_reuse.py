"""Derivation reuse remains bounded without reusing resource authority."""

import math
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agentive.harness import capability_search as search
from app.services.retrieval import definition_vectors as store


@pytest.fixture
def metadata_store(monkeypatch):
    rows = {}
    reads = []

    async def find(collection, query, *, limit):
        assert collection == "object"
        ids = query["id"]["$in"]
        assert len(ids) <= store.READ_BATCH and limit == len(ids)
        reads.append(ids)
        return [rows[identity] for identity in ids if identity in rows]

    async def save(space, text, vector):
        identity, digest = store.vector_identity(space, text)
        rows[identity] = {
            "id": identity,
            "entity": "DefinitionVector",
            "context": {"space": space, "digest": digest, "vector": list(vector)},
        }

    monkeypatch.setattr(
        store,
        "get_default_context",
        lambda: SimpleNamespace(database=SimpleNamespace(find=find)),
    )
    monkeypatch.setattr(store, "save_vector", save)
    monkeypatch.setattr(
        "app.services.retrieval.index_definition.embedding_profile",
        lambda: SimpleNamespace(id="encoder-revision-a"),
    )
    search._DEFINITION_VECTORS.clear()
    yield rows, reads
    search._DEFINITION_VECTORS.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [511, 512, 513, 1024])
async def test_catalogue_cliff_restart_and_single_definition_update(
    metadata_store, monkeypatch, size
):
    rows, reads = metadata_store
    encoder = AsyncMock(return_value=[1.0, 0.5])
    monkeypatch.setattr(search, "_embed_text_for_capability_search", encoder)
    texts = [f"Capability schema revision one {i}" for i in range(size)]
    first = await search._definition_vectors(texts)
    assert encoder.await_count == size
    assert len(search._DEFINITION_VECTORS) <= 512
    reads.clear()
    assert await search._definition_vectors(texts) == first
    assert encoder.await_count == size
    assert len(reads) <= math.ceil(size / store.READ_BATCH)
    # Simulates another worker/restart, retaining only the primary derivations.
    search._DEFINITION_VECTORS.clear()
    assert await search._definition_vectors(texts) == first
    assert encoder.await_count == size
    texts[0] += " changed schema"
    await search._definition_vectors(texts)
    assert encoder.await_count == size + 1
    assert len(rows) == size + 1
    # A different encoder artifact/revision must never reuse an old vector space.
    monkeypatch.setattr(
        "app.services.retrieval.index_definition.embedding_profile",
        lambda: SimpleNamespace(id="encoder-revision-b"),
    )
    await search._definition_vectors(texts)
    assert encoder.await_count == 2 * size + 1


@pytest.mark.asyncio
async def test_only_requested_definitions_are_loaded_and_copies_are_isolated(
    metadata_store, monkeypatch
):
    encoder = AsyncMock(return_value=[1.0, 0.5])
    monkeypatch.setattr(search, "_embed_text_for_capability_search", encoder)
    await search._definition_vectors(["admitted", "removed"])
    result = await search._definition_vectors(["admitted"])
    assert len(result) == 1
    result[0][0] = 99
    assert await search._definition_vectors(["admitted"]) == [[1.0, 0.5]]
    assert encoder.await_count == 2


@pytest.mark.asyncio
async def test_corrupt_and_nonfinite_derivations_are_not_reused(
    metadata_store, monkeypatch
):
    rows, _ = metadata_store
    identity, digest = store.vector_identity("encoder-revision-a", "schema")
    rows[identity] = {
        "id": identity,
        "entity": "DefinitionVector",
        "context": {
            "space": "encoder-revision-a",
            "digest": digest,
            "vector": [float("nan")],
        },
    }
    encoder = AsyncMock(return_value=[1.0, 0.5])
    monkeypatch.setattr(search, "_embed_text_for_capability_search", encoder)
    assert await search._definition_vectors(["schema"]) == [[1.0, 0.5]]
    assert encoder.await_count == 1
    search._DEFINITION_VECTORS.clear()
    with pytest.raises(ValueError):
        await search._definition_vectors(["s"] * (store.MAX_DEFINITIONS + 1))


@pytest.mark.asyncio
async def test_real_object_round_trip_has_no_plaintext_or_graph_membership():
    await store.save_vector("test-encoder", "private description", [1.0, 0.5])
    assert await store.read_vectors("test-encoder", ["private description"]) == {
        "private description": [1.0, 0.5]
    }
    assert await store.read_vectors("different-encoder", ["private description"]) == {}


@pytest.mark.asyncio
async def test_retention_removes_only_expired_derivations_in_bounded_batches():
    from app.models.definition_vector import DefinitionVector

    for index in range(3):
        await DefinitionVector(
            space="expired",
            digest=str(index),
            vector=[1.0],
            created_at="2000-01-01T00:00:00+00:00",
        ).save()
    await store.save_vector("fresh", "still useful", [0.5])
    assert await store.prune_definition_vectors(limit=2) == 2
    assert await store.prune_definition_vectors(limit=2) == 1
    assert await store.read_vectors("fresh", ["still useful"]) == {
        "still useful": [0.5]
    }


def test_scalar_metadata_cache_accepts_large_provider_vectors_without_ann_limits():
    assert store.valid_vector([0.1] * 3072)
    assert not store.valid_vector([0.1] * 8193)


@pytest.mark.asyncio
async def test_high_dimensional_cohorts_obey_working_and_cache_memory_budgets(
    metadata_store, monkeypatch
):
    monkeypatch.setattr(
        "app.services.retrieval.index_definition.embedding_profile",
        lambda: SimpleNamespace(id="large-space", dimensions=3072),
    )
    encoder = AsyncMock(return_value=[0.1] * 3072)
    monkeypatch.setattr(search, "_embed_text_for_capability_search", encoder)
    with pytest.raises(ValueError, match="memory budget"):
        await search._definition_vectors([f"Description {n}" for n in range(700)])
    encoder.assert_not_awaited()
    await search._definition_vectors([f"Description {n}" for n in range(128)])
    assert (
        sum(map(len, search._DEFINITION_VECTORS.values()))
        <= store.MAX_CACHED_COMPONENTS
    )
    before = encoder.await_count
    search._DEFINITION_VECTORS.clear()
    await search._definition_vectors([f"Description {n}" for n in range(128)])
    assert encoder.await_count == before
