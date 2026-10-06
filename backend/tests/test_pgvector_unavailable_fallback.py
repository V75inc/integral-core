"""Known pgvector deployment gaps degrade safely to graph retrieval."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest


class _Connection:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def execute(self, _query: str) -> None:
        raise self.error


class _Pool:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


@pytest.mark.asyncio
async def test_missing_pgvector_extension_is_normalized_at_driver_boundary():
    """A server without the vector extension signals known unavailability."""
    import asyncpg

    from app.services.retrieval.embedding_store import EmbeddingStoreUnavailable
    from app.services.retrieval.pgvector_driver import PgVectorEmbeddingStore

    original = asyncpg.exceptions.FeatureNotSupportedError(
        'extension "vector" is not available'
    )
    driver = PgVectorEmbeddingStore(dsn="postgresql://invalid", table="test_embed")

    with pytest.raises(EmbeddingStoreUnavailable) as raised:
        await driver._ensure_schema(_Pool(_Connection(original)))

    assert raised.value.__cause__ is original
    assert "pgvector extension" in str(raised.value)


@pytest.mark.asyncio
async def test_other_pgvector_schema_failures_are_not_reclassified():
    """Permission and unrelated schema failures must retain their cause."""
    import asyncpg

    from app.services.retrieval.embedding_store import EmbeddingStoreUnavailable
    from app.services.retrieval.pgvector_driver import PgVectorEmbeddingStore

    original = asyncpg.exceptions.InsufficientPrivilegeError("permission denied")
    driver = PgVectorEmbeddingStore(dsn="postgresql://invalid", table="test_embed")

    with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError) as raised:
        await driver._ensure_schema(_Pool(_Connection(original)))

    assert raised.value is original
    assert not isinstance(raised.value, EmbeddingStoreUnavailable)


@pytest.mark.asyncio
async def test_known_store_unavailability_degrades_to_graph(monkeypatch):
    """The retrieval boundary falls back once without hiding the outage."""
    from app.api import retrieve
    from app.schemas.policy import Subject
    from app.schemas.retrieve import RetrieveRequest
    from app.services.retrieval.embedding_store import EmbeddingStoreUnavailable

    disabled: list[str] = []

    async def unavailable(*_args: Any, **_kwargs: Any):
        raise EmbeddingStoreUnavailable("pgvector extension is unavailable")

    async def graph(*_args: Any, **_kwargs: Any):
        return ([], 0, 0)

    monkeypatch.setattr(retrieve, "_retrieve_hybrid", unavailable)
    monkeypatch.setattr(retrieve, "_retrieve_graph", graph)
    monkeypatch.setattr(
        retrieve,
        "disable_semantic_retrieval_runtime",
        lambda reason: disabled.append(reason),
    )
    monkeypatch.setattr(retrieve, "_warn_degraded_once", lambda _mode: None)

    result = await retrieve._dispatch_retrieve_modes(
        Subject(kind="human", id="u1"),
        RetrieveRequest(query="equipment maintenance", mode="hybrid"),
        requested_mode="hybrid",
        effective_mode="hybrid",
        degraded=False,
        k=20,
        top_n=5,
    )

    assert result == ([], 0, 0, "graph", True)
    assert disabled == ["pgvector extension is unavailable"]
