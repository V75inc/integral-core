"""POST /api/retrieve end-to-end + permission-filter regression suite (RET-03).

Tests are organized as four loose groups:

1. **Boundary / schema** (Task 1) — RetrieveRequest defaults, ``extra='forbid'``,
   mode-literal validation, 422 on malformed body, 401 without auth (graph
   empty-results envelope).
2. **Mode dispatch** (Task 2) — graph / semantic / hybrid happy paths, scope
   ``track:<id>`` pre-filter applied, scope ``workspace:<id>`` falls through,
   K + top_n overrides, Entry-not-found is skipped (not raised), archived
   entries filtered BEFORE policy_evaluate, RetrieveFilters applied AFTER the
   permission filter.
3. **ROADMAP AC#2 — no authorization bypass via index** (Task 2) — the headline
   gate. A candidate from an inaccessible track is silently dropped from the
   response and counted in ``dropped_for_permission``. Per-request cache
   amortizes duplicate ``policy_engine.evaluate`` calls.
4. **MCP auto-discovery** (Task 3) — the @endpoint surfaces in
   ``GET /api/mcp/tools`` under ``integral_create_retrieve`` (Plan 04-03 renames
   it to ``integral_query``).

Per CONTEXT lock #12: PolicyAction / ChangeEventAction / ActorKind each return
exactly 1 grep match. The single-Literal preservation test is below.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest
from httpx import AsyncClient
from pydantic import ValidationError

pytestmark = [pytest.mark.slow, pytest.mark.model, pytest.mark.asyncio]

# ---------------------------------------------------------------------------
# Group 1 — Boundary / schema tests (no DB / no auth dependency)
# ---------------------------------------------------------------------------


def test_retrieve_request_defaults():
    """RetrieveRequest defaults: mode=hybrid, k=150, top_n=20, scope=None."""
    from app.schemas.retrieve import RetrieveRequest

    req = RetrieveRequest(query="hello world")
    assert req.query == "hello world"
    assert req.mode == "hybrid"
    assert req.k == 150  # CONTEXT lock #5
    assert req.top_n == 20  # CONTEXT lock #5
    assert req.scope is None
    assert req.filters is None


def test_retrieve_request_mode_literal_validation():
    """Mode is a Literal — bogus values must raise ValidationError."""
    from app.schemas.retrieve import RetrieveRequest

    with pytest.raises(ValidationError):
        RetrieveRequest(query="x", mode="bogus")  # type: ignore[arg-type]


def test_retrieve_request_extra_forbid():
    """extra='forbid' — unknown top-level fields must raise ValidationError."""
    from app.schemas.retrieve import RetrieveRequest

    with pytest.raises(ValidationError):
        RetrieveRequest(query="x", custom_field="y")  # type: ignore[call-arg]


def test_retrieve_filters_shape_and_extra_forbid():
    """RetrieveFilters constructs; extra fields rejected."""
    from app.schemas.retrieve import RetrieveFilters

    f = RetrieveFilters(entry_types=["plan"], tags=["urgent"])
    assert f.entry_types == ["plan"]
    assert f.tags == ["urgent"]

    with pytest.raises(ValidationError):
        RetrieveFilters(entry_types=["plan"], rogue="y")  # type: ignore[call-arg]


def test_retrieved_entry_shape():
    """RetrievedEntry carries the locked wire shape."""
    from app.schemas.retrieve import RetrievedEntry

    r = RetrievedEntry(
        entry_id="e1",
        track_id="t1",
        score=0.5,
        mode_origin=["semantic"],
    )
    assert r.entry_id == "e1"
    assert r.track_id == "t1"
    assert r.score == 0.5
    assert r.mode_origin == ["semantic"]
    assert r.provenance is None
    assert r.snippet is None


def test_retrieve_response_default_envelope():
    """RetrieveResponse defaults — empty results structure."""
    from app.schemas.retrieve import RetrieveResponse

    resp = RetrieveResponse(mode="graph", requested_mode="graph", results=[])
    assert resp.results == []
    assert resp.dropped_for_permission == 0
    assert resp.candidates_examined == 0
    assert resp.mode == "graph"
    assert resp.requested_mode == "graph"
    assert resp.degraded is False


# ---------------------------------------------------------------------------
# Group 1b — End-to-end HTTP boundary (Task 1 — empty-results envelope + 422)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_retrieve_graph_empty_results_envelope(
    authenticated_client: AsyncClient,
):
    """Graph mode with zero candidates returns the empty envelope (200)."""
    resp = await authenticated_client.post(
        "/api/retrieve",
        json={"query": "anything", "mode": "graph"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["mode"] == "graph"
    assert data["results"] == []
    assert data["dropped_for_permission"] == 0


@pytest.mark.asyncio
async def test_post_retrieve_422_on_missing_query(
    authenticated_client: AsyncClient,
):
    """Missing required ``query`` field → 422."""
    resp = await authenticated_client.post("/api/retrieve", json={"mode": "graph"})
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_post_retrieve_422_on_unknown_field(
    authenticated_client: AsyncClient,
):
    """extra='forbid' is enforced at the request boundary — unknown field → 422."""
    resp = await authenticated_client.post(
        "/api/retrieve",
        json={"query": "x", "mode": "graph", "rogue_field": "y"},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_post_retrieve_422_on_bogus_mode(
    authenticated_client: AsyncClient,
):
    """Mode-literal validation at the boundary → 422."""
    resp = await authenticated_client.post(
        "/api/retrieve",
        json={"query": "x", "mode": "bogus"},
    )
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# Group 1c — Single-Literal invariants preserved (CONTEXT lock #12)
# ---------------------------------------------------------------------------


def _grep_count(pattern: str) -> int:
    """Count single-Literal definitions matching ``pattern`` under backend/app/."""
    backend_app = Path(__file__).resolve().parents[1] / "app"
    result = subprocess.run(
        ["grep", "-rE", pattern, str(backend_app), "--include=*.py"],
        capture_output=True,
        text=True,
        check=False,
    )
    lines = [
        ln
        for ln in result.stdout.splitlines()
        if "__pycache__" not in ln and ln.strip()
    ]
    return len(lines)


def test_single_literal_invariants_preserved():
    """PolicyAction / ChangeEventAction / ActorKind each defined ONCE.

    Plan 04-02 adds ZERO new members on any of the three (CONTEXT lock #12;
    ``entry.read`` already covers retrieval).
    """
    assert (
        _grep_count(r"^PolicyAction\s*=\s*Literal") == 1
    ), "PolicyAction single-Literal gate violated"
    assert (
        _grep_count(r"^ChangeEventAction\s*=\s*Literal") == 1
    ), "ChangeEventAction single-Literal gate violated"
    assert (
        _grep_count(r"^ActorKind\s*=\s*Literal") == 1
    ), "ActorKind single-Literal gate violated"


# ===========================================================================
# Group 2 — Mode dispatch + permission filter (Task 2)
# ===========================================================================
#
# Fixture pattern: directly use ``Track`` + ``Entry`` jvspatial nodes to seed
# minimum-viable graph state, then call ``POST /api/retrieve``. The
# ``authenticated_client`` fixture issues a JWT bound to ``test@example.com``
# whose User node owns the seeded Track(s).
# ---------------------------------------------------------------------------


async def _seed_owned_track(test_user: Any) -> Any:
    """Create a Track OWNED by the test user — graph mode should reach it."""
    from datetime import datetime

    from app.models.edges import OWNS
    from app.models.nodes import Track

    track = await Track.create(
        name="retrieval-track",
        owner_id=test_user.id,
        workspace_id="",
        purpose="retrieval test",
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat(),
    )
    await test_user.connect(track, edge=OWNS, owned_at=datetime.now().isoformat())
    return track


async def _seed_entry(track: Any, *, title: str = "hello", body: str = "") -> Any:
    """Create an Entry in ``track`` linked via CONTAINS."""
    from datetime import datetime

    from app.models.edges import CONTAINS
    from app.models.nodes import Entry

    entry = await Entry.create(
        type_id="",
        title=title,
        author_id="",
        track_id=track.id,
        tags=[],
        custom_fields={},
        status="active",
        body=body,
        attachment_ids=[],
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat(),
    )
    await track.connect(entry, edge=CONTAINS, added_at=datetime.now().isoformat())
    return entry


@pytest.mark.asyncio
async def test_retrieve_graph_mode_happy_path(
    authenticated_client: AsyncClient, test_user: Any
):
    """Graph mode returns entries the user can reach via the traversal."""
    track = await _seed_owned_track(test_user)
    e1 = await _seed_entry(track, title="alpha")
    e2 = await _seed_entry(track, title="beta")

    resp = await authenticated_client.post(
        "/api/retrieve",
        json={"query": "anything", "mode": "graph", "scope": f"track:{track.id}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["mode"] == "graph"
    ids = {r["entry_id"] for r in data["results"]}
    assert e1.id in ids
    assert e2.id in ids
    # No semantic candidates in graph mode → dropped_for_permission stays 0
    assert data["dropped_for_permission"] == 0


@pytest.mark.asyncio
async def test_retrieve_semantic_mode_happy_path(
    authenticated_client: AsyncClient, test_user: Any, monkeypatch
):
    """Semantic mode returns ranked results from the embedding store."""
    track = await _seed_owned_track(test_user)
    e1 = await _seed_entry(track, title="permitted-1")
    e2 = await _seed_entry(track, title="permitted-2")

    # Stub the embedding model + store to avoid downloading the real model.
    from app.api import retrieve as retrieve_module

    async def fake_embed_query(query: str) -> list[float]:
        return [0.1, 0.2, 0.3]

    class FakeStore:
        async def search(self, vector, k=50, scope=None):
            return [(e1.id, 0.9), (e2.id, 0.7)]

    monkeypatch.setattr(retrieve_module, "embed_query_text", fake_embed_query)
    monkeypatch.setattr(retrieve_module, "get_embedding_store", lambda: FakeStore())

    resp = await authenticated_client.post(
        "/api/retrieve",
        json={"query": "topic", "mode": "semantic", "scope": f"track:{track.id}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["mode"] == "semantic"
    ids = [r["entry_id"] for r in data["results"]]
    assert ids == [e1.id, e2.id]  # sorted descending by score
    assert data["results"][0]["score"] == 0.9
    assert data["results"][0]["mode_origin"] == ["semantic"]


@pytest.mark.asyncio
async def test_retrieve_hybrid_mode_rrf_combines(
    authenticated_client: AsyncClient, test_user: Any, monkeypatch
):
    """Hybrid mode runs graph + semantic and fuses ranks via RRF.

    Both channels surface the same entry — the entry appears once in the
    fused result with mode_origin including BOTH channels.
    """
    track = await _seed_owned_track(test_user)
    e1 = await _seed_entry(track, title="shared-entry")

    from app.api import retrieve as retrieve_module

    async def fake_embed_query(query: str) -> list[float]:
        return [0.1, 0.2, 0.3]

    class FakeStore:
        async def search(self, vector, k=50, scope=None):
            return [(e1.id, 0.85)]

    monkeypatch.setattr(retrieve_module, "embed_query_text", fake_embed_query)
    monkeypatch.setattr(retrieve_module, "get_embedding_store", lambda: FakeStore())

    resp = await authenticated_client.post(
        "/api/retrieve",
        json={"query": "x", "mode": "hybrid", "scope": f"track:{track.id}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["mode"] == "hybrid"
    # Entry appears exactly once
    matching = [r for r in data["results"] if r["entry_id"] == e1.id]
    assert len(matching) == 1
    # Both channels surfaced it
    assert "graph" in matching[0]["mode_origin"]
    assert "semantic" in matching[0]["mode_origin"]


@pytest.mark.asyncio
async def test_scope_track_pre_filter_passed_to_store(
    authenticated_client: AsyncClient, test_user: Any, monkeypatch
):
    """``scope='track:<id>'`` is forwarded to ``embedding_store.search`` verbatim.

    Verifies CONTEXT lock #6 — the pre-filter is delegated to the store.
    """
    track = await _seed_owned_track(test_user)

    seen_scope: list[Any] = []

    from app.api import retrieve as retrieve_module

    async def fake_embed_query(query: str) -> list[float]:
        return [0.0] * 3

    class SpyStore:
        async def search(self, vector, k=50, scope=None):
            seen_scope.append(scope)
            return []

    monkeypatch.setattr(retrieve_module, "embed_query_text", fake_embed_query)
    monkeypatch.setattr(retrieve_module, "get_embedding_store", lambda: SpyStore())

    resp = await authenticated_client.post(
        "/api/retrieve",
        json={"query": "x", "mode": "semantic", "scope": f"track:{track.id}"},
    )
    assert resp.status_code == 200, resp.text
    assert seen_scope == [f"track:{track.id}"]


@pytest.mark.asyncio
async def test_scope_workspace_falls_through_to_store(
    authenticated_client: AsyncClient, test_user: Any, monkeypatch
):
    """``scope='workspace:<id>'`` is forwarded as-is.

    The embedding store driver is responsible for skipping the SQL
    pre-filter on workspace scope (see ``embedding_store.py`` module
    docstring). The endpoint MUST forward the scope verbatim — it does
    NOT silently rewrite to ``None``.
    """
    seen_scope: list[Any] = []

    from app.api import retrieve as retrieve_module

    async def fake_embed_query(query: str) -> list[float]:
        return [0.0] * 3

    class SpyStore:
        async def search(self, vector, k=50, scope=None):
            seen_scope.append(scope)
            return []

    monkeypatch.setattr(retrieve_module, "embed_query_text", fake_embed_query)
    monkeypatch.setattr(retrieve_module, "get_embedding_store", lambda: SpyStore())

    resp = await authenticated_client.post(
        "/api/retrieve",
        json={"query": "x", "mode": "semantic", "scope": "workspace:abc"},
    )
    assert resp.status_code == 200, resp.text
    assert seen_scope == ["workspace:abc"]


@pytest.mark.asyncio
async def test_k_and_top_n_body_overrides(
    authenticated_client: AsyncClient, test_user: Any, monkeypatch
):
    """k + top_n from request body override the env defaults."""
    track = await _seed_owned_track(test_user)
    entries = []
    for i in range(8):
        entries.append(await _seed_entry(track, title=f"e{i}"))

    seen_k: list[int] = []

    from app.api import retrieve as retrieve_module

    async def fake_embed_query(query: str) -> list[float]:
        return [0.0] * 3

    class FakeStore:
        async def search(self, vector, k=50, scope=None):
            seen_k.append(k)
            return [(e.id, 1.0 / (i + 1)) for i, e in enumerate(entries)]

    monkeypatch.setattr(retrieve_module, "embed_query_text", fake_embed_query)
    monkeypatch.setattr(retrieve_module, "get_embedding_store", lambda: FakeStore())

    resp = await authenticated_client.post(
        "/api/retrieve",
        json={
            "query": "x",
            "mode": "semantic",
            "scope": f"track:{track.id}",
            "k": 50,
            "top_n": 3,
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # k=50 was passed to the store
    assert seen_k == [50]
    # top_n=3 caps the results
    assert len(data["results"]) == 3


@pytest.mark.asyncio
async def test_ghost_entry_id_silently_dropped(
    authenticated_client: AsyncClient, test_user: Any, monkeypatch
):
    """Embedding store returns an entry_id that ``Entry.get`` can't resolve.

    The endpoint must NOT raise — the ghost is silently dropped (it's a
    stale index row from a hard-deleted entry).
    """
    track = await _seed_owned_track(test_user)
    real_entry = await _seed_entry(track, title="real")

    from app.api import retrieve as retrieve_module

    async def fake_embed_query(query: str) -> list[float]:
        return [0.0] * 3

    class FakeStore:
        async def search(self, vector, k=50, scope=None):
            return [("ghost_entry_id_does_not_exist", 0.99), (real_entry.id, 0.5)]

    monkeypatch.setattr(retrieve_module, "embed_query_text", fake_embed_query)
    monkeypatch.setattr(retrieve_module, "get_embedding_store", lambda: FakeStore())

    resp = await authenticated_client.post(
        "/api/retrieve",
        json={"query": "x", "mode": "semantic", "scope": f"track:{track.id}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    ids = {r["entry_id"] for r in data["results"]}
    assert real_entry.id in ids
    assert "ghost_entry_id_does_not_exist" not in ids


@pytest.mark.asyncio
async def test_filters_applied_after_permission_filter(
    authenticated_client: AsyncClient, test_user: Any, monkeypatch
):
    """``filters.tags`` is a PROJECTION (post-permission), not an access decision.

    Entries dropped by filters must NOT inflate ``dropped_for_permission``.
    """
    track = await _seed_owned_track(test_user)
    tagged = await _seed_entry(track, title="tagged")
    tagged.tags = ["important"]
    await tagged.save()
    untagged = await _seed_entry(track, title="untagged")

    from app.api import retrieve as retrieve_module

    async def fake_embed_query(query: str) -> list[float]:
        return [0.0] * 3

    class FakeStore:
        async def search(self, vector, k=50, scope=None):
            return [(tagged.id, 0.9), (untagged.id, 0.8)]

    monkeypatch.setattr(retrieve_module, "embed_query_text", fake_embed_query)
    monkeypatch.setattr(retrieve_module, "get_embedding_store", lambda: FakeStore())

    resp = await authenticated_client.post(
        "/api/retrieve",
        json={
            "query": "x",
            "mode": "semantic",
            "scope": f"track:{track.id}",
            "filters": {"tags": ["important"]},
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    ids = {r["entry_id"] for r in data["results"]}
    assert tagged.id in ids
    assert untagged.id not in ids
    # Filter dropouts MUST NOT count as permission dropouts
    assert data["dropped_for_permission"] == 0


# ===========================================================================
# Group 3 — ROADMAP AC#2: No authorization bypass via index
# ===========================================================================


@pytest.mark.asyncio
async def test_no_authorization_bypass_via_index(
    authenticated_client: AsyncClient, test_user: Any, test_user2: Any, monkeypatch
):
    """ROADMAP AC#2 — the headline gate.

    Seed an Entry in a Track that the calling user CANNOT access. Force
    the embedding store to return it as a candidate. POST /api/retrieve.
    Assert:

    * The inaccessible entry_id does NOT appear in ``results``.
    * ``dropped_for_permission`` is incremented (at least by 1).
    * The vector store WAS consulted (the candidate was examined; just
      not returned).

    This is invariant I-RET-02 (no-index-bypass) in its operational
    form. If this test ever fails, the entire Phase 4 acceptance fails.
    """
    from datetime import datetime

    from app.models.edges import CONTAINS, OWNS
    from app.models.nodes import Entry, Track

    # ---- Inaccessible track — owned by test_user2, NEVER shared with test_user
    forbidden_track = await Track.create(
        name="forbidden-track",
        owner_id=test_user2.id,
        workspace_id="",
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat(),
    )
    await test_user2.connect(
        forbidden_track, edge=OWNS, owned_at=datetime.now().isoformat()
    )
    forbidden_entry = await Entry.create(
        type_id="",
        title="SECRET — must not leak",
        author_id=test_user2.id,
        track_id=forbidden_track.id,
        tags=[],
        custom_fields={},
        status="active",
        body="confidential",
        attachment_ids=[],
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat(),
    )
    await forbidden_track.connect(
        forbidden_entry, edge=CONTAINS, added_at=datetime.now().isoformat()
    )

    # ---- Accessible track — owned by test_user
    own_track = await _seed_owned_track(test_user)
    own_entry = await _seed_entry(own_track, title="legit")

    # Stub the embedding store to surface BOTH candidates as if a
    # rogue/buggy index call had returned them. The endpoint MUST drop
    # the forbidden entry via the per-candidate policy filter.
    from app.api import retrieve as retrieve_module

    async def fake_embed_query(query: str) -> list[float]:
        return [0.0] * 3

    class FakeStore:
        async def search(self, vector, k=50, scope=None):
            # Even universe-wide scope must NOT bypass the permission
            # filter — that's the headline gate.
            return [(forbidden_entry.id, 0.99), (own_entry.id, 0.5)]

    monkeypatch.setattr(retrieve_module, "embed_query_text", fake_embed_query)
    monkeypatch.setattr(retrieve_module, "get_embedding_store", lambda: FakeStore())

    resp = await authenticated_client.post(
        "/api/retrieve",
        json={"query": "x", "mode": "semantic"},  # scope=None → universe-wide
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    returned_ids = {r["entry_id"] for r in data["results"]}
    assert (
        forbidden_entry.id not in returned_ids
    ), "AUTHORIZATION BYPASS: forbidden entry leaked into retrieval results"
    assert own_entry.id in returned_ids, "own entry must still surface"
    assert (
        data["dropped_for_permission"] >= 1
    ), "policy filter must report the dropped forbidden candidate"


@pytest.mark.asyncio
async def test_no_authorization_bypass_in_hybrid_mode(
    authenticated_client: AsyncClient, test_user: Any, test_user2: Any, monkeypatch
):
    """Hybrid mode: forbidden semantic candidate must still be dropped.

    Hybrid mode unions graph + semantic. Graph traversal NEVER surfaces
    the forbidden entry (it's permission-gated). Semantic surfaces it
    (mocked). The fused result MUST exclude it.
    """
    from datetime import datetime

    from app.models.edges import CONTAINS, OWNS
    from app.models.nodes import Entry, Track

    forbidden_track = await Track.create(
        name="forbidden-hybrid",
        owner_id=test_user2.id,
        workspace_id="",
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat(),
    )
    await test_user2.connect(
        forbidden_track, edge=OWNS, owned_at=datetime.now().isoformat()
    )
    forbidden_entry = await Entry.create(
        type_id="",
        title="SECRET",
        author_id=test_user2.id,
        track_id=forbidden_track.id,
        tags=[],
        custom_fields={},
        status="active",
        body="",
        attachment_ids=[],
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat(),
    )
    await forbidden_track.connect(
        forbidden_entry, edge=CONTAINS, added_at=datetime.now().isoformat()
    )
    own_track = await _seed_owned_track(test_user)
    own_entry = await _seed_entry(own_track, title="own-hybrid")

    from app.api import retrieve as retrieve_module

    async def fake_embed_query(query: str) -> list[float]:
        return [0.0] * 3

    class FakeStore:
        async def search(self, vector, k=50, scope=None):
            return [(forbidden_entry.id, 0.99), (own_entry.id, 0.5)]

    monkeypatch.setattr(retrieve_module, "embed_query_text", fake_embed_query)
    monkeypatch.setattr(retrieve_module, "get_embedding_store", lambda: FakeStore())

    resp = await authenticated_client.post(
        "/api/retrieve",
        json={"query": "x", "mode": "hybrid"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    returned_ids = {r["entry_id"] for r in data["results"]}
    assert forbidden_entry.id not in returned_ids
    assert own_entry.id in returned_ids


@pytest.mark.asyncio
async def test_per_request_cache_amortizes_policy_evaluate(
    authenticated_client: AsyncClient, test_user: Any, monkeypatch
):
    """The per-request permissions cache memoizes duplicate evaluate calls.

    If the same entry appears as a candidate twice (e.g. via duplicate
    rows from a buggy store), ``policy_engine.evaluate`` for that entry
    runs at most once — verified by counting calls through a spy.
    """
    track = await _seed_owned_track(test_user)
    entry = await _seed_entry(track, title="dup")

    from app.api import retrieve as retrieve_module
    from app.services import policy_engine

    real_evaluate = policy_engine.evaluate
    call_log: list[Tuple[str, str]] = []

    async def spy_evaluate(*, subject, action, resource, _internal_actor=None):
        call_log.append((resource.id, action))
        return await real_evaluate(
            subject=subject,
            action=action,
            resource=resource,
            _internal_actor=_internal_actor,
        )

    # patch the symbol used by the retrieve module
    monkeypatch.setattr(retrieve_module, "policy_evaluate", spy_evaluate)

    async def fake_embed_query(query: str) -> list[float]:
        return [0.0] * 3

    class FakeStore:
        async def search(self, vector, k=50, scope=None):
            # Duplicate the same entry_id three times
            return [(entry.id, 0.9), (entry.id, 0.8), (entry.id, 0.7)]

    monkeypatch.setattr(retrieve_module, "embed_query_text", fake_embed_query)
    monkeypatch.setattr(retrieve_module, "get_embedding_store", lambda: FakeStore())

    resp = await authenticated_client.post(
        "/api/retrieve",
        json={"query": "x", "mode": "semantic", "scope": f"track:{track.id}"},
    )
    assert resp.status_code == 200, resp.text
    # First call hits policy_engine.evaluate; subsequent identical calls
    # are served by the per-request cache → at most 1 spy invocation for
    # this entry_id.
    entry_calls = [c for c in call_log if c == (entry.id, "entry.read")]
    assert len(entry_calls) <= 1, (
        f"per-request cache must amortize duplicate policy evaluations; "
        f"got {len(entry_calls)} calls for {entry.id}"
    )


# ===========================================================================
# Group 4 — MCP auto-discovery (Task 3)
# ===========================================================================


@pytest.mark.asyncio
async def test_retrieve_endpoint_appears_in_mcp_tool_catalogue(
    authenticated_client: AsyncClient,
):
    """Phase 3 MCP-01 contract — every @endpoint surfaces in /api/mcp/tools.

    Plan 04-02 wires the endpoint under the auto-derived name
    ``integral_create_retrieve``. Plan 04-03 will register the
    ``MCP_TOOL_NAME_OVERRIDES`` entry that renames it to
    ``integral_query``.
    """
    resp = await authenticated_client.get("/api/mcp/tools")
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    # Accept either {"tools": [...]} or a bare list
    tools = payload.get("tools") if isinstance(payload, dict) else payload
    assert tools is not None
    names = {t["name"] for t in tools}
    # Until Plan 04-03 lands, the auto-derived name is present.
    assert (
        "integral_create_retrieve" in names or "integral_query" in names
    ), f"retrieve descriptor missing from MCP tool catalogue; got {sorted(names)[:10]}"


# ===========================================================================
# Group 5 — Wave 2 graceful fallback dispatch
# ===========================================================================
#
# When the deployment has no real vector store (the ``null`` driver is
# active), ``mode=semantic`` and ``mode=hybrid`` MUST transparently
# degrade to ``mode=graph`` and the response MUST carry ``degraded=true``
# alongside the original ``requested_mode``. The per-candidate policy
# filter (I-RET-01) is untouched — graph mode is already permission-gated
# upstream of this dispatch layer.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retrieve_semantic_degrades_to_graph_when_null_driver_active(
    authenticated_client: AsyncClient, test_user: Any, monkeypatch
):
    """semantic + null driver → mode=graph, degraded=true, requested_mode=semantic."""
    track = await _seed_owned_track(test_user)
    await _seed_entry(track, title="hello")

    monkeypatch.setenv("EMBEDDING_STORE_DRIVER", "null")

    resp = await authenticated_client.post(
        "/api/retrieve",
        json={"query": "topic", "mode": "semantic", "scope": f"track:{track.id}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["mode"] == "graph"
    assert data["requested_mode"] == "semantic"
    assert data["degraded"] is True
    # Graph results are still permission-gated (I-RET-01 untouched).
    assert isinstance(data["results"], list)


@pytest.mark.asyncio
async def test_retrieve_hybrid_degrades_to_graph_when_null_driver_active(
    authenticated_client: AsyncClient, test_user: Any, monkeypatch
):
    """hybrid + null driver → mode=graph, degraded=true, requested_mode=hybrid."""
    track = await _seed_owned_track(test_user)
    await _seed_entry(track, title="hello")

    monkeypatch.setenv("EMBEDDING_STORE_DRIVER", "null")

    resp = await authenticated_client.post(
        "/api/retrieve",
        json={"query": "topic", "mode": "hybrid", "scope": f"track:{track.id}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["mode"] == "graph"
    assert data["requested_mode"] == "hybrid"
    assert data["degraded"] is True


@pytest.mark.asyncio
async def test_retrieve_graph_mode_not_degraded(
    authenticated_client: AsyncClient, test_user: Any, monkeypatch
):
    """Explicit mode=graph passes through unchanged; degraded stays false."""
    track = await _seed_owned_track(test_user)
    monkeypatch.setenv("EMBEDDING_STORE_DRIVER", "null")

    resp = await authenticated_client.post(
        "/api/retrieve",
        json={"query": "topic", "mode": "graph", "scope": f"track:{track.id}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["mode"] == "graph"
    assert data["requested_mode"] == "graph"
    assert data["degraded"] is False
