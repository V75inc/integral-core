"""App-scoped passage facade on ToolContext."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.schemas.retrieve import RetrievedEntry, RetrievedPassage
from app.services.hooks.registry import ToolContext


class _Entry:
    def __init__(self, entry_id: str, title: str) -> None:
        self.id = entry_id
        self.title = title
        self.record_revision = 2


def _ctx() -> ToolContext:
    ctx = ToolContext(
        user_id="u1",
        workspace_id="w1",
        scope="tool",
        actor_kind="human",
    )
    ctx.app_id = "app-tcs"
    return ctx


def _result(entry_id: str, title: str) -> RetrievedEntry:
    return RetrievedEntry(
        entry_id=entry_id,
        track_id="track-inv",
        score=0.91,
        title=title,
        source_revision=2,
        mode_origin=["graph", "semantic"],
        passages=[
            RetrievedPassage(
                field="custom_fields.description",
                start=0,
                end=12,
                text="filter fabric",
                tokens=2,
            )
        ],
    )


def test_search_entries_narrows_to_eligible_ids(monkeypatch):
    ctx = _ctx()
    entries = [_Entry("e1", "NW18"), _Entry("e2", "SG80")]
    calls = {}

    async def tracks(track_type):
        assert track_type == "inventory"
        return [SimpleNamespace(id="track-inv")]

    async def allowed(track_type, entry_type=None):
        assert track_type == "inventory"
        assert entry_type == "inventory_item"
        return entries

    async def retrieve(subject, payload, *, driver, k, top_n, eligible_entry_ids=None):
        calls["eligible"] = eligible_entry_ids
        assert payload.scope == "app:app-tcs"
        assert payload.mode == "hybrid"
        return [_result("e1", "NW18")], 0, 1, "hybrid", False

    class _Driver:
        pass

    import app.services.retrieval.pgvector_driver as pg
    import app.services.retrieval.search_service as search_service

    monkeypatch.setattr(ctx, "_tracks_by_title", tracks)
    monkeypatch.setattr(ctx, "find_entries_in_track_type", allowed)
    monkeypatch.setattr(pg, "PgVectorEmbeddingStore", _Driver)
    monkeypatch.setattr("app.services.retrieval.get_embedding_store", lambda: _Driver())
    monkeypatch.setattr(search_service, "retrieve_passages", retrieve)

    result = asyncio.run(
        ctx.search_entries(
            query="filter fabric",
            track_type="inventory",
            entry_type="inventory_item",
            eligible_entry_ids=["e1", "e2", "outside"],
            limit=10,
        )
    )

    assert result["available"] is True
    assert result["effective_mode"] == "hybrid"
    assert result["coverage"] == "full"
    assert result["candidate_limit_reached"] is False
    assert calls["eligible"] == ["e1", "e2"]
    assert result["results"][0]["entry_id"] == "e1"
    assert result["results"][0]["passages"][0]["text"] == "filter fabric"
    assert "ineligible_ids_dropped" in result["warnings"]


def test_search_entries_rejects_missing_app_scope():
    ctx = ToolContext(user_id="u1", workspace_id="w1", scope="tool")

    result = asyncio.run(ctx.search_entries(query="geotextile", track_type="inventory"))

    assert result["available"] is False
    assert result["reason"] == "app_scope_required"


def test_search_entries_caps_eligible_ids(monkeypatch):
    ctx = _ctx()
    entries = [_Entry(f"e{i}", f"Item {i}") for i in range(600)]
    calls = {}

    async def tracks(_track_type):
        return [SimpleNamespace(id="track-inv")]

    async def allowed(_track_type, _entry_type=None):
        return entries

    async def retrieve(subject, payload, *, driver, k, top_n, eligible_entry_ids=None):
        calls["eligible"] = eligible_entry_ids
        return [], 0, 0, "hybrid", False

    class _Driver:
        pass

    import app.services.retrieval.pgvector_driver as pg
    import app.services.retrieval.search_service as search_service

    monkeypatch.setattr(ctx, "_tracks_by_title", tracks)
    monkeypatch.setattr(ctx, "find_entries_in_track_type", allowed)
    monkeypatch.setattr(pg, "PgVectorEmbeddingStore", _Driver)
    monkeypatch.setattr("app.services.retrieval.get_embedding_store", lambda: _Driver())
    monkeypatch.setattr(search_service, "retrieve_passages", retrieve)

    ids = [f"e{i}" for i in range(600)]
    result = asyncio.run(
        ctx.search_entries(
            query="item",
            track_type="inventory",
            eligible_entry_ids=ids,
        )
    )

    assert len(calls["eligible"]) == 500
    assert result["candidate_limit_reached"] is True
    assert result["coverage"] == "partial"
    assert "eligible_ids_capped" in result["warnings"]
