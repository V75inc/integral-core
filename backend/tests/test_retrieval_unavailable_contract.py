"""Unavailable search is not an empty search or an implicit wider read."""

from unittest.mock import AsyncMock, Mock

import pytest

from app.agentive.tooling.dispatch import _tool_error_from_exception
from app.api import retrieve as handler
from app.api.errors import InsufficientPermissionsError, RetrievalUnavailableError
from app.schemas.retrieve import RetrieveRequest
from app.services.retrieval.embedding_store import EmbeddingStoreUnavailable
from app.services.retrieval.pgvector_driver import PgVectorEmbeddingStore

pytestmark = pytest.mark.smoke


@pytest.mark.asyncio
@pytest.mark.parametrize("attachment", [None, "document"])
async def test_missing_lexical_store_is_explicit_and_does_not_broaden(
    monkeypatch, attachment
):
    from app.services.retrieval import search_service

    driver = object.__new__(PgVectorEmbeddingStore)
    monkeypatch.setattr(handler, "get_embedding_store", lambda: driver)
    search = AsyncMock(
        side_effect=EmbeddingStoreUnavailable("private database diagnostic")
    )
    fallback = AsyncMock()
    disabled = Mock()
    monkeypatch.setattr(search_service, "retrieve_passages", search)
    monkeypatch.setattr(handler, "_retrieve_graph", fallback)
    monkeypatch.setattr(handler, "disable_semantic_retrieval_runtime", disabled)
    payload = RetrieveRequest(
        query="source evidence", mode="lexical", attachment_kind=attachment
    )
    with pytest.raises(RetrievalUnavailableError) as error:
        await handler._dispatch_retrieve_modes(
            object(),
            payload,
            requested_mode="lexical",
            effective_mode="lexical",
            degraded=False,
            k=10,
            top_n=5,
        )
    receipt = _tool_error_from_exception(error.value)
    assert receipt.is_error and receipt.error_code == "retrieval_unavailable"
    assert "not completed" in receipt.message
    assert "known record ID" in receipt.message
    assert "private database" not in receipt.message
    fallback.assert_not_awaited()
    disabled.assert_not_called()


def test_legacy_store_error_uses_same_sanitized_tool_envelope():
    receipt = _tool_error_from_exception(
        EmbeddingStoreUnavailable("postgresql://private")
    )
    assert receipt.is_error
    assert receipt.error_code == "retrieval_unavailable"
    assert "postgresql" not in receipt.message
    assert not receipt.next_tool


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [ValueError("bug"), InsufficientPermissionsError()])
async def test_non_capability_errors_are_not_hidden_as_unavailable(monkeypatch, error):
    def fail():
        raise error

    monkeypatch.setattr(handler, "get_embedding_store", fail)
    with pytest.raises(type(error)):
        await handler._dispatch_retrieve_modes(
            object(),
            RetrieveRequest(query="evidence", mode="lexical"),
            requested_mode="lexical",
            effective_mode="lexical",
            degraded=False,
            k=10,
            top_n=5,
        )
