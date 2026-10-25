"""Passage selector narrows to an eligible-ID set without widening scope."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.services.retrieval.index_definition import EmbeddingProfile
from app.services.retrieval.passage_store import PassageStore


class _Conn:
    def __init__(self) -> None:
        self.sql = ""
        self.args = ()
        self.queries = []

    async def fetch(self, sql, *args):
        self.sql = sql
        self.args = args
        self.queries.append((sql, args))
        return []

    async def fetchval(self, sql, *args):
        self.queries.append((sql, args))
        return 0

    async def execute(self, sql, *args):
        return None

    def transaction(self):
        return _Acquire(self)


class _Acquire:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *args):
        return None


class _Pool:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn

    def acquire(self):
        return _Acquire(self.conn)


@pytest.mark.parametrize("mode", ["semantic", "graph", "hybrid"])
def test_search_passes_eligible_ids_to_the_selector(mode) -> None:
    conn = _Conn()
    store = PassageStore(
        SimpleNamespace(_table="retrieval_test"),
        EmbeddingProfile(
            model="test",
            revision="test",
            dimensions=384,
            chunk_tokens=160,
            overlap_tokens=24,
        ),
    )

    async def schema():
        return _Pool(conn)

    store.ensure_schema = schema  # type: ignore[method-assign]

    result = asyncio.run(
        store.search(
            [0.0] * 384,
            workspace_id="w1",
            track_ids=["t1"],
            k=10,
            query="filter fabric",
            mode=mode,
            eligible_entry_ids=["e1", "e2"],
        )
    )

    assert result == []
    assert conn.queries
    for sql, args in conn.queries:
        assert "$8::text[] IS NULL OR entry_id=ANY($8::text[])" in sql
        assert args[7] == ["e1", "e2"]
        assert args[8] is False


def test_search_without_eligible_ids_passes_no_filter() -> None:
    conn = _Conn()
    store = PassageStore(
        SimpleNamespace(_table="retrieval_test"),
        EmbeddingProfile(
            model="test",
            revision="test",
            dimensions=384,
            chunk_tokens=160,
            overlap_tokens=24,
        ),
    )

    async def schema():
        return _Pool(conn)

    store.ensure_schema = schema  # type: ignore[method-assign]

    asyncio.run(
        store.search(
            [0.0] * 384,
            workspace_id="w1",
            track_ids=["t1"],
            k=10,
        )
    )

    assert conn.args[7] is None


def test_explicit_empty_ids_return_before_store_access():
    store = PassageStore(
        SimpleNamespace(_table="retrieval_test"),
        EmbeddingProfile(
            model="test",
            revision="test",
            dimensions=384,
            chunk_tokens=160,
            overlap_tokens=24,
        ),
    )

    async def forbidden():
        raise AssertionError("Empty selection must not query the store")

    store.ensure_schema = forbidden
    assert (
        asyncio.run(
            store.search(
                [0.0] * 384,
                workspace_id="w1",
                track_ids=["t1"],
                k=10,
                eligible_entry_ids=[],
            )
        )
        == []
    )


@pytest.mark.parametrize("k,branch_limit", [(1, 3), (10, 30), (150, 450), (500, 500)])
def test_diversity_overfetch_is_bounded_per_branch(k, branch_limit):
    conn = _Conn()
    store = PassageStore(
        SimpleNamespace(_table="retrieval_test"),
        EmbeddingProfile(
            model="test",
            revision="test",
            dimensions=384,
            chunk_tokens=160,
            overlap_tokens=24,
        ),
    )

    async def schema():
        return _Pool(conn)

    store.ensure_schema = schema
    assert (
        asyncio.run(
            store.search(
                [0.0] * 384,
                workspace_id="w1",
                track_ids=["t1"],
                k=k,
                query="project records",
                mode="hybrid",
            )
        )
        == []
    )
    assert conn.queries
    assert all(args[4] == branch_limit for _, args in conn.queries)
