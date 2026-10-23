"""Passage selector narrows to an eligible-ID set without widening scope."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.services.retrieval.index_definition import EmbeddingProfile
from app.services.retrieval.passage_store import PassageStore


class _Conn:
    def __init__(self) -> None:
        self.sql = ""
        self.args = ()

    async def fetch(self, sql, *args):
        self.sql = sql
        self.args = args
        return []


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


def test_search_passes_eligible_ids_to_the_selector() -> None:
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
            mode="hybrid",
            eligible_entry_ids=["e1", "e2"],
        )
    )

    assert result == []
    assert "cardinality($9::text[]) = 0" in conn.sql
    assert conn.args[-1] == ["e1", "e2"]


def test_search_without_eligible_ids_passes_an_empty_set() -> None:
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

    assert conn.args[-1] == []
