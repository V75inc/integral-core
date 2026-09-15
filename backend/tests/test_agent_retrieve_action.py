"""jvagent ``retrieve_context`` agent action regression — Plan 04-03 Task 2.

Per Plan 04-02 downstream_handoff: the agent action wraps ``POST /api/retrieve``
via DIRECT service-function invocation (NOT an in-process HTTP client) — this
bypasses the JWT round-trip and lets the agent's Subject(kind='agent', id=...)
flow through cleanly so the per-candidate ``policy_engine.evaluate`` runs
under the AGENT's policy (Phase 3 POL-02 narrower-than-human contract).

Per CONTEXT lock #11: agent action lives inside the AGENTIVE_ENABLED
conditional load path (backend/app/agentive/) and registers in the
``backend/app/agentive/agent_actions/__init__.py`` registry — discoverable
via the same surface as other harness actions.

Per CONTEXT lock #12: ZERO new PolicyAction / ChangeEventAction / ActorKind
members. Subject.kind='agent' reuses the existing Literal.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, List, Tuple

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _grep_count(pattern: str) -> int:
    """Count code-line matches (excluding __pycache__).

    Uses Python ``re`` rather than shelling out to ``grep`` so the gate is
    portable across GNU grep builds that do not treat ``\\s`` as whitespace.
    """
    backend_app = Path(__file__).resolve().parents[1] / "app"
    rx = re.compile(pattern)
    count = 0
    for py in backend_app.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        for line in py.read_text(encoding="utf-8").splitlines():
            if rx.search(line):
                count += 1
    return count


# ---------------------------------------------------------------------------
# Section A — Registry registration + import surface
# ---------------------------------------------------------------------------


def test_retrieve_context_importable():
    """retrieve_context is importable from the agent_actions package."""
    from app.agentive.agent_actions import retrieve_context  # noqa: F401

    assert callable(retrieve_context)


def test_retrieve_context_registered_in_registry():
    """retrieve_context appears in get_registered_actions() after import."""
    import app.agentive.agent_actions as agent_actions  # noqa: F401 — eager registration

    registered = agent_actions.get_registered_actions()
    assert (
        "retrieve_context" in registered
    ), f"retrieve_context not registered. Registered: {list(registered.keys())}"


def test_register_action_rejects_duplicates():
    """register_action raises ValueError on duplicate name."""
    import app.agentive.agent_actions as agent_actions

    async def noop(**_kwargs: Any) -> Any:
        return None

    with pytest.raises(ValueError):
        agent_actions.register_action("retrieve_context", noop)


# ---------------------------------------------------------------------------
# Section B — Happy-path invocation + Subject construction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retrieve_context_happy_path_returns_response(monkeypatch):
    """retrieve_context returns a RetrieveResponse with the channel results."""
    import importlib

    action_module = importlib.import_module(
        "app.agentive.agent_actions.retrieve_context"
    )
    from app.agentive.agent_actions.retrieve_context import (  # noqa: F401
        retrieve_context,
    )
    from app.schemas.retrieve import RetrievedEntry, RetrieveResponse

    canned: List[RetrievedEntry] = [
        RetrievedEntry(
            entry_id="e1",
            track_id="t1",
            score=0.9,
            mode_origin=["semantic"],
        )
    ]

    async def fake_dispatch(
        subject, payload, *, requested_mode, effective_mode, degraded, k, top_n
    ):
        return (canned, 0, 1, effective_mode, degraded)

    monkeypatch.setattr(action_module, "_dispatch_retrieve_modes", fake_dispatch)

    resp = await retrieve_context(agent_id="agent-1", query="hello", mode="hybrid")
    assert isinstance(resp, RetrieveResponse)
    assert resp.mode == "hybrid"
    assert resp.candidates_examined == 1
    assert len(resp.results) == 1
    assert resp.results[0].entry_id == "e1"


@pytest.mark.asyncio
async def test_retrieve_context_constructs_agent_subject(monkeypatch):
    """The Subject passed to the mode handler is Subject(kind='agent', id=agent_id)."""
    import importlib

    action_module = importlib.import_module(
        "app.agentive.agent_actions.retrieve_context"
    )
    from app.agentive.agent_actions.retrieve_context import retrieve_context

    captured: dict = {}

    async def fake_dispatch(
        subject, payload, *, requested_mode, effective_mode, degraded, k, top_n
    ):
        captured["subject"] = subject
        captured["payload"] = payload
        captured["k"] = k
        captured["top_n"] = top_n
        return ([], 0, 0, effective_mode, degraded)

    monkeypatch.setattr(action_module, "_dispatch_retrieve_modes", fake_dispatch)

    await retrieve_context(agent_id="agent-XYZ", query="topic", mode="semantic")

    assert captured["subject"].kind == "agent"
    assert captured["subject"].id == "agent-XYZ"


@pytest.mark.asyncio
async def test_retrieve_context_dispatch_graph(monkeypatch):
    """mode='graph' dispatches to _retrieve_graph."""
    import importlib

    action_module = importlib.import_module(
        "app.agentive.agent_actions.retrieve_context"
    )
    from app.agentive.agent_actions.retrieve_context import retrieve_context

    captured: dict = {}

    async def fake_dispatch(
        subject, payload, *, requested_mode, effective_mode, degraded, k, top_n
    ):
        captured["requested_mode"] = requested_mode
        captured["effective_mode"] = effective_mode
        return ([], 0, 0, effective_mode, degraded)

    monkeypatch.setattr(action_module, "_dispatch_retrieve_modes", fake_dispatch)

    await retrieve_context(agent_id="a1", query="x", mode="graph")
    assert captured["requested_mode"] == "graph"
    assert captured["effective_mode"] == "graph"


@pytest.mark.asyncio
async def test_retrieve_context_default_mode_is_hybrid(monkeypatch):
    """Default mode is 'hybrid' (mirrors RetrieveRequest default)."""
    import importlib

    action_module = importlib.import_module(
        "app.agentive.agent_actions.retrieve_context"
    )
    from app.agentive.agent_actions.retrieve_context import retrieve_context

    captured: dict = {}

    async def fake_dispatch(
        subject, payload, *, requested_mode, effective_mode, degraded, k, top_n
    ):
        captured["effective_mode"] = effective_mode
        return ([], 0, 0, effective_mode, degraded)

    monkeypatch.setattr(action_module, "_dispatch_retrieve_modes", fake_dispatch)

    resp = await retrieve_context(agent_id="a1", query="x")
    assert captured["effective_mode"] == "hybrid"
    assert resp.mode == "hybrid"


# ---------------------------------------------------------------------------
# Section C — Validation + error paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retrieve_context_empty_agent_id_raises():
    """retrieve_context requires a non-empty agent_id."""
    from app.agentive.agent_actions.retrieve_context import retrieve_context

    with pytest.raises(ValueError):
        await retrieve_context(agent_id="", query="hello")


@pytest.mark.asyncio
async def test_retrieve_context_passes_scope_and_filters(monkeypatch):
    """scope + filters reach the mode handler unchanged."""
    import importlib

    action_module = importlib.import_module(
        "app.agentive.agent_actions.retrieve_context"
    )
    from app.agentive.agent_actions.retrieve_context import retrieve_context
    from app.schemas.retrieve import RetrieveFilters

    captured: dict = {}

    async def fake_dispatch(
        subject, payload, *, requested_mode, effective_mode, degraded, k, top_n
    ):
        captured["payload"] = payload
        return ([], 0, 0, effective_mode, degraded)

    monkeypatch.setattr(action_module, "_dispatch_retrieve_modes", fake_dispatch)

    filters = RetrieveFilters(entry_types=["plan"], tags=["urgent"])
    await retrieve_context(
        agent_id="a1",
        query="x",
        scope="track:t-99",
        filters=filters,
        mode="hybrid",
        k=50,
        top_n=5,
    )

    payload = captured["payload"]
    assert payload.scope == "track:t-99"
    assert payload.filters is not None
    assert payload.filters.entry_types == ["plan"]
    assert payload.filters.tags == ["urgent"]
    assert payload.k == 50
    assert payload.top_n == 5


# ---------------------------------------------------------------------------
# Section D — Single-Literal invariants preserved (CONTEXT lock #12)
# ---------------------------------------------------------------------------


def test_single_literal_invariants_preserved():
    """Plan 04-03 adds ZERO members to PolicyAction / ChangeEventAction / ActorKind."""
    assert (
        _grep_count(r"^PolicyAction\s*=\s*Literal") == 1
    ), "PolicyAction single-Literal gate violated"
    assert (
        _grep_count(r"^ChangeEventAction\s*=\s*Literal") == 1
    ), "ChangeEventAction single-Literal gate violated"
    assert (
        _grep_count(r"^ActorKind\s*=\s*Literal") == 1
    ), "ActorKind single-Literal gate violated"


# ---------------------------------------------------------------------------
# Section E — Direct service-function invocation (NOT HTTP client)
# ---------------------------------------------------------------------------


def test_action_uses_direct_service_invocation_not_http():
    """Plan 04-02 downstream_handoff: DIRECT service-function invocation only.

    The agent action MUST NOT import httpx / requests / fastapi.testclient
    or otherwise open a network round-trip back to the in-process app —
    that would double the JWT cost and break the agent-Subject flow.
    """
    import importlib

    action_mod = importlib.import_module("app.agentive.agent_actions.retrieve_context")

    src = Path(action_mod.__file__).read_text()
    # Disallow HTTP client imports
    assert "import httpx" not in src, "agent action must NOT import httpx"
    assert "from httpx" not in src, "agent action must NOT use httpx"
    assert "import requests" not in src, "agent action must NOT import requests"
    assert "TestClient" not in src, "agent action must NOT use FastAPI TestClient"
    # Must call retrieve service functions directly (not HTTP)
    assert (
        "_dispatch_retrieve_modes" in src
    ), "agent action must import _dispatch_retrieve_modes from app.api.retrieve"
    assert "from app.api.retrieve import" in src
