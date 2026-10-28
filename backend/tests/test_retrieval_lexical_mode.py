"""The public keyword channel uses the scoped index without model inference."""

from types import SimpleNamespace

import pytest

from app.api import retrieve as retrieval_api
from app.schemas.retrieve import RetrieveRequest
from app.services.retrieval.pgvector_driver import PgVectorEmbeddingStore

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio]


async def test_lexical_dispatch_uses_passages_without_semantic_encoding(monkeypatch):
    from app.services.retrieval import search_service

    driver = object.__new__(PgVectorEmbeddingStore)
    monkeypatch.setattr(retrieval_api, "get_embedding_store", lambda: driver)
    observed = {}

    async def search(subject, payload, **kwargs):
        observed.update(mode=payload.mode, subject=subject, **kwargs)
        return [SimpleNamespace(mode_origin=["graph"])], 0, 1, "graph", False

    monkeypatch.setattr(search_service, "retrieve_passages", search)
    subject = SimpleNamespace(id="reader")
    result = await retrieval_api._dispatch_retrieve_modes(
        subject,
        RetrieveRequest(query="example", mode="lexical"),
        requested_mode="lexical",
        effective_mode="lexical",
        degraded=False,
        k=10,
        top_n=5,
    )
    assert result[0][0].mode_origin == ["lexical"]
    assert result[1:] == (0, 1, "lexical", False)
    assert observed["mode"] == "graph"
    assert observed["subject"] is subject
    assert observed["driver"] is driver


async def test_lexical_falls_back_to_canonical_search_without_a_passage_store(
    monkeypatch,
):
    monkeypatch.setattr(retrieval_api, "get_embedding_store", lambda: object())

    async def search(subject, payload, **kwargs):
        assert payload.mode == "graph"
        return [SimpleNamespace(mode_origin=["graph"])], 0, 1

    monkeypatch.setattr(retrieval_api, "_retrieve_graph", search)
    result = await retrieval_api._dispatch_retrieve_modes(
        SimpleNamespace(id="reader"),
        RetrieveRequest(query="example", mode="lexical"),
        requested_mode="lexical",
        effective_mode="lexical",
        degraded=False,
        k=10,
        top_n=5,
    )
    assert result[0][0].mode_origin == ["lexical"]
    assert result[1:] == (0, 1, "lexical", True)
