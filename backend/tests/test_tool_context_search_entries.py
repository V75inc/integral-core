"""Bounded App passage facade, including delegated principals and safe failures."""

from types import SimpleNamespace

import pytest

from app.schemas.retrieve import RetrievedEntry, RetrievedPassage
from app.services.app_operations.context import OperationContext
from app.services.hooks.registry import ToolContext
from app.services.retrieval import app_search

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio]


def _ctx():
    ctx = ToolContext(user_id="u1", workspace_id="w1", scope="tool")
    ctx.app_id = "app1"
    return ctx


def _result():
    return RetrievedEntry(
        entry_id="e1",
        track_id="t1",
        score=0.9,
        title="Item",
        source_revision=2,
        mode_origin=["graph", "semantic"],
        action_url="/tracks/t1?entry=e1",
        passages=[
            RetrievedPassage(field="body", start=0, end=6, text="fabric", tokens=1)
        ],
    )


@pytest.fixture
def search(monkeypatch):
    from app.services.retrieval import pgvector_driver, search_service

    calls = {}

    class Driver:
        pass

    async def candidates(ctx, track_type, entry_type, supplied, *, metadata_only=False):
        calls["selection"] = (ctx, track_type, entry_type, supplied)
        calls["metadata_only"] = metadata_only
        return ({"t1": None} if metadata_only else ["e1", "e2"]), False

    async def retrieve(subject, payload, **kwargs):
        calls.setdefault("retrieval", []).append((subject, payload, kwargs))
        return [_result()], 0, 1, payload.mode, False

    monkeypatch.setattr(app_search, "_candidate_ids", candidates)
    monkeypatch.setattr(pgvector_driver, "PgVectorEmbeddingStore", Driver)
    monkeypatch.setattr("app.services.retrieval.get_embedding_store", lambda: Driver())
    monkeypatch.setattr(search_service, "retrieve_passages", retrieve)
    return calls


async def test_search_narrows_ids_and_does_not_assert_index_coverage(search):
    result = await _ctx().search_entries(
        query="fabric",
        track_type="inventory",
        entry_type="item",
        eligible_entry_ids=["e1", "e2", "outside", "e1"],
        limit=10,
    )
    assert search["selection"][1:] == ("inventory", "item", ["e1", "e2", "outside"])
    subject, payload, kwargs = search["retrieval"][0]
    assert (subject.kind, subject.id) == ("human", "u1")
    assert payload.scope == "app:app1" and kwargs["eligible_entry_ids"] == ["e1", "e2"]
    assert result["coverage"] == "unknown"
    assert not result["candidate_limit_reached"]
    assert "ineligible_ids_dropped" in result["warnings"]
    assert result["results"][0]["action_url"] == "/tracks/t1?entry=e1"


@pytest.mark.parametrize(
    "kwargs", [{"query": ""}, {"eligible_entry_ids": []}, {"eligible_entry_ids": [" "]}]
)
async def test_empty_search_does_not_touch_graph_or_provider(search, kwargs):
    result = await _ctx().search_entries(
        **{"query": "fabric", "track_type": "inventory", **kwargs}
    )
    assert result["results"] == [] and result["coverage"] == "full"
    assert not search


async def test_missing_app_never_searches(search):
    ctx = ToolContext(user_id="u1", workspace_id="w1", scope="tool")
    result = await ctx.search_entries(query="fabric", track_type="inventory")
    assert result["reason"] == "app_scope_required" and not search


async def test_caps_input_before_selection_and_reports_partial(search):
    ids = [f"e{i}" for i in range(600)]
    result = await _ctx().search_entries(
        query="fabric", track_type="inventory", eligible_entry_ids=ids
    )
    assert len(search["selection"][3]) == 500
    assert result["coverage"] == "partial" and result["candidate_limit_reached"]
    assert "eligible_ids_capped" in result["warnings"]


@pytest.mark.parametrize("kind", ["agent", "connector"])
async def test_exact_service_actor_keeps_bounded_human_audience(
    search, monkeypatch, kind
):
    from app.services import app_operation_authority

    calls = []

    async def author(**kwargs):
        calls.append(kwargs)
        return "u1"

    monkeypatch.setattr(app_operation_authority, "current_operation_actor", author)
    ctx = OperationContext(
        user_id="u1",
        workspace_id="w1",
        scope="operation",
        app_id="app1",
        actor_kind=kind,
        actor_id="service1",
        operation_key="lookup",
        read_only=True,
    )
    result = await ctx.search_entries(query="fabric", track_type="inventory")
    assert result["results"]
    assert all(
        (c["subject"].kind, c["subject"].id) == (kind, "service1") for c in calls
    )
    assert len(calls) >= 2
    subject = search["retrieval"][0][0]
    assert (subject.kind, subject.id) == ("human", "u1")


async def test_bare_agent_cannot_borrow_human_reads(search):
    ctx = _ctx()
    ctx.actor_kind = "agent"
    result = await ctx.search_entries(query="fabric", track_type="inventory")
    assert not result["available"] and result["results"] == [] and not search


async def test_service_revocation_prevents_result_release(search, monkeypatch):
    from app.services import app_operation_authority

    calls = []

    async def author(**kwargs):
        calls.append(kwargs)
        return "u1" if len(calls) == 1 else ""

    monkeypatch.setattr(app_operation_authority, "current_operation_actor", author)
    ctx = _ctx()
    ctx.actor_kind, ctx.actor_id, ctx.operation_key = "agent", "service1", "lookup"
    result = await ctx.search_entries(query="fabric", track_type="inventory")
    assert not result["available"] and result["results"] == []
    assert len(search["retrieval"]) == 1 and len(calls) == 2


@pytest.mark.parametrize("error", [RuntimeError, ValueError])
async def test_private_exception_text_never_leaves_facade(search, monkeypatch, error):
    from app.services.retrieval import search_service

    async def broken(*args, **kwargs):
        raise error("PRIVATE_PROVIDER_CREDENTIAL_SENTINEL")

    monkeypatch.setattr(search_service, "retrieve_passages", broken)
    result = await _ctx().search_entries(query="fabric", track_type="inventory")
    assert not result["available"]
    assert "PRIVATE_PROVIDER_CREDENTIAL_SENTINEL" not in str(result)
    assert result["reason"] in {"search_failed", "invalid_request"}


async def test_fallback_is_explicit_and_workspace_scope_is_restored(
    search, monkeypatch
):
    from app.services.agent_scope import current_scope_workspace_id
    from app.services.retrieval import search_service

    calls = []

    async def retrieve(subject, payload, **kwargs):
        assert current_scope_workspace_id.get() == "w1"
        calls.append(payload.mode)
        if payload.mode == "hybrid":
            raise RuntimeError("provider unavailable")
        return [_result()], 0, 1, "graph", False

    monkeypatch.setattr(search_service, "retrieve_passages", retrieve)
    token = current_scope_workspace_id.set("outside")
    try:
        result = await _ctx().search_entries(query="fabric", track_type="inventory")
        assert current_scope_workspace_id.get() == "outside"
    finally:
        current_scope_workspace_id.reset(token)
    assert calls == ["hybrid", "graph"]
    assert result["degraded"] and result["coverage"] == "partial"
    assert "semantic_unavailable" in result["warnings"]


async def test_candidate_limit_is_detected_before_result_count(search):
    result = await _ctx().search_entries(
        query="fabric", track_type="inventory", limit=1
    )
    assert search["retrieval"][0][1].top_n == 1
    assert result["candidate_limit_reached"] and result["coverage"] == "partial"


@pytest.mark.parametrize("mode", ["semantic", "graph"])
async def test_requested_mode_is_preserved(search, mode):
    result = await _ctx().search_entries(
        query="fabric", track_type="inventory", mode=mode
    )
    assert result["requested_mode"] == mode == search["retrieval"][0][1].mode


async def test_scope_failure_is_stable_and_non_widening(search, monkeypatch):
    async def absent(*args, **kwargs):
        raise app_search._SearchUnavailable("track_not_found")

    monkeypatch.setattr(app_search, "_candidate_ids", absent)
    result = await _ctx().search_entries(query="fabric", track_type="absent")
    assert not result["available"] and result["reason"] == "track_not_found"
    assert "retrieval" not in search


async def test_discovery_passes_governed_scope_to_ranking_instead_of_first_ids(search):
    result = await _ctx().search_entries(query="fabric", track_type="inventory")
    assert result["results"] and search["metadata_only"] is True
    kwargs = search["retrieval"][0][2]
    assert kwargs["eligible_entry_ids"] is None
    assert kwargs["type_selection"] == {"t1": None}


async def test_selector_change_during_ranking_discards_the_old_evidence(
    search, monkeypatch
):
    calls = 0

    async def changing(*args, **kwargs):
        nonlocal calls
        calls += 1
        return ({"t1": None} if calls == 1 else {"t2": None}), False

    monkeypatch.setattr(app_search, "_candidate_ids", changing)
    result = await _ctx().search_entries(query="fabric", track_type="inventory")
    assert not result["available"] and result["reason"] == "selection_changed"
    assert result["results"] == []
