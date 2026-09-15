"""Regression suite for Plan 04-01 Task 2 — re-embed / soft-delete hooks
wired into the existing ``app/api/entries.py`` write + delete paths.

Behaviours mirror Plan 04-01 task 2 <behavior> tags (8 cases):

1. create_entry triggers embedding_store.upsert
2. update_entry triggers embedding_store.upsert
3. delete_entry triggers embedding_store.soft_delete (NOT hard_delete)
4. re-embed failure does NOT roll back the parent write
5. tag-only mutation does NOT trigger re-embed
6. startup eager-load — load_model invoked iff EMBEDDING_MODEL_EAGER_LOAD=1
7. INVARIANTS.md retains the new retrieval invariants
8. single-Literal grep gates remain at exactly 1 each (PolicyAction,
   ChangeEventAction, ActorKind)

The hooks call ``app.services.retrieval.get_embedding_store()`` and
``app.services.retrieval.embedding_model.embed_entry_text``. Tests
monkeypatch both so no real model load / vector store touches happen
inside CI — the contract is the call shape, not the math.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.model]

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
INVARIANTS = REPO_ROOT / "docs" / "INVARIANTS.md"


# ---------------------------------------------------------------------------
# Hook-site tests — exercise the helper functions directly. Doing the full
# HTTP round-trip is overkill for hook-presence checks and noisy because
# create_entry needs a populated graph; the helpers are the unit of
# behaviour the plan introduces, so we test them directly.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reembed_entry_calls_upsert(monkeypatch):
    """create_entry / update_entry both reach the embedding store via
    _reembed_entry. Verify the helper calls upsert with the entry's
    track_id + type_id metadata."""

    from app.api import entries as entries_mod
    from app.services.retrieval import embedding_model

    fake_store = MagicMock()
    fake_store.upsert = AsyncMock(return_value=None)
    monkeypatch.setattr(entries_mod, "get_embedding_store", lambda: fake_store)
    monkeypatch.setattr(
        embedding_model,
        "_MODEL",
        SimpleNamespace(
            encode=lambda text, convert_to_numpy=False: [0.1]
            * embedding_model.EMBEDDING_DIM
        ),
    )

    entry = SimpleNamespace(
        id="E123",
        title="Hello",
        body="World",
        track_id="T1",
        type_id="note",
        custom_fields={},
    )
    await entries_mod._reembed_entry(entry)

    assert fake_store.upsert.await_count == 1
    call_kwargs = fake_store.upsert.await_args.kwargs
    assert call_kwargs["entry_id"] == "E123"
    assert call_kwargs["metadata"]["track_id"] == "T1"
    assert call_kwargs["metadata"]["type_id"] == "note"
    assert isinstance(call_kwargs["vector"], list)
    assert len(call_kwargs["vector"]) == embedding_model.EMBEDDING_DIM


@pytest.mark.asyncio
async def test_soft_delete_embedding_calls_soft_delete(monkeypatch):
    """delete_entry routes through _soft_delete_embedding, which MUST
    call store.soft_delete — NEVER hard_delete (I-RET-03)."""

    from app.api import entries as entries_mod

    fake_store = MagicMock()
    fake_store.soft_delete = AsyncMock(return_value=None)
    fake_store.hard_delete = AsyncMock(return_value=None)
    monkeypatch.setattr(entries_mod, "get_embedding_store", lambda: fake_store)

    await entries_mod._soft_delete_embedding("EZAP")

    assert fake_store.soft_delete.await_count == 1
    assert fake_store.soft_delete.await_args.args == ("EZAP",)
    assert fake_store.hard_delete.await_count == 0


@pytest.mark.asyncio
async def test_reembed_failure_does_not_propagate(monkeypatch):
    """If embed_entry_text or store.upsert raises, _reembed_entry MUST
    swallow the exception (mirror of policy_engine.py denial-emit
    discipline) — the parent write IS NOT rolled back."""

    from app.api import entries as entries_mod
    from app.services.retrieval import embedding_model

    fake_store = MagicMock()
    fake_store.upsert = AsyncMock(side_effect=RuntimeError("vec store down"))
    monkeypatch.setattr(entries_mod, "get_embedding_store", lambda: fake_store)
    monkeypatch.setattr(
        embedding_model,
        "_MODEL",
        SimpleNamespace(
            encode=lambda text, convert_to_numpy=False: [0.0]
            * embedding_model.EMBEDDING_DIM
        ),
    )

    entry = SimpleNamespace(
        id="E999",
        title="t",
        body="b",
        track_id="T",
        type_id="",
        custom_fields={},
    )
    # MUST NOT raise.
    await entries_mod._reembed_entry(entry)


@pytest.mark.asyncio
async def test_soft_delete_failure_does_not_propagate(monkeypatch):
    from app.api import entries as entries_mod

    fake_store = MagicMock()
    fake_store.soft_delete = AsyncMock(side_effect=RuntimeError("vec store down"))
    monkeypatch.setattr(entries_mod, "get_embedding_store", lambda: fake_store)

    await entries_mod._soft_delete_embedding("E_FAIL")  # MUST NOT raise


# ---------------------------------------------------------------------------
# Hook call-site presence — assert the three sites in entries.py do call
# the helpers (string grep on source guarantees they were inserted; a
# downstream regression that deletes one of them lights up immediately).
# ---------------------------------------------------------------------------


def test_three_hook_sites_present_in_entries_py():
    src = (BACKEND_DIR / "app" / "api" / "entries.py").read_text()
    # _reembed_entry must be called at exactly two sites (create + update);
    # _soft_delete_embedding at exactly one (delete). The helper definitions
    # also contain the names, so we expect at least 2 + 1 + 2 (defs) calls
    # of the substrings. Tighter check: the helper-call invocations all
    # have the form ``await _reembed_entry(entry)`` etc.
    assert src.count("await _reembed_entry(entry)") == 2, (
        "expected exactly 2 await _reembed_entry(entry) call sites "
        "(create + update); count was different"
    )
    assert (
        src.count("await _soft_delete_embedding(entry_id)") == 1
    ), "expected exactly 1 await _soft_delete_embedding(entry_id) call site (delete)"


def test_tag_endpoints_do_not_call_reembed():
    """CONTEXT lock #4 — tag mutations skip re-embed. The four tag /
    reaction handlers (add_tag_to_entry, remove_tag_from_entry,
    add_reaction, remove_reaction) MUST NOT contain a re-embed call."""

    src = (BACKEND_DIR / "app" / "api" / "entries.py").read_text()

    # Split on `@endpoint` decorators so we can inspect each handler body
    # in isolation.
    segments = src.split("@endpoint(")
    tag_segments = [
        seg
        for seg in segments
        if "/tags" in seg.split("\n", 1)[0] or "/reactions" in seg.split("\n", 1)[0]
    ]
    assert tag_segments, "expected at least one tag/reaction endpoint segment"
    for seg in tag_segments:
        assert (
            "_reembed_entry(" not in seg
        ), "tag/reaction handler must not call _reembed_entry (CONTEXT lock #4)"


# ---------------------------------------------------------------------------
# Startup eager-load — Test 6
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_startup_eager_load_invokes_load_model(monkeypatch):
    """When EMBEDDING_MODEL_EAGER_LOAD is set to ``"1"`` the warmup
    coroutine MUST call load_model exactly once."""

    from app.services.retrieval import embedding_model as em

    monkeypatch.setenv("EMBEDDING_MODEL_EAGER_LOAD", "1")
    calls = {"count": 0}

    def _fake_load():
        calls["count"] += 1
        return object()

    monkeypatch.setattr(em, "load_model", _fake_load)

    from app import main as app_main

    await app_main._warm_embedding_model()
    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_startup_skips_load_when_flag_off(monkeypatch):
    from app.services.retrieval import embedding_model as em

    monkeypatch.setenv("EMBEDDING_MODEL_EAGER_LOAD", "0")
    calls = {"count": 0}

    def _fake_load():
        calls["count"] += 1
        return object()

    monkeypatch.setattr(em, "load_model", _fake_load)

    from app import main as app_main

    await app_main._warm_embedding_model()
    assert calls["count"] == 0


# ---------------------------------------------------------------------------
# Invariants regressions — Tests 7 + 8
# ---------------------------------------------------------------------------


def test_invariants_md_contains_retrieval_section():
    txt = INVARIANTS.read_text()
    # Five new I-RET invariants land in this plan.
    for marker in (
        "permission-filter-at-retrieval",
        "no-index-bypass",
        "soft-delete-not-hard-delete",
        "I-RET-01",
        "I-RET-02",
        "I-RET-03",
        "I-RET-04",
        "I-RET-05",
    ):
        assert marker in txt, f"INVARIANTS.md missing marker {marker!r}"


def _grep_count(pattern: str) -> int:
    """Count files containing a top-level ``<Name> = Literal`` declaration."""

    result = subprocess.run(
        [
            "grep",
            "-rE",
            pattern,
            str(BACKEND_DIR / "app"),
            "--include=*.py",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    lines = [line for line in result.stdout.splitlines() if "__pycache__" not in line]
    return len(lines)


def test_single_literal_invariants_preserved():
    """Phase 4 adds zero new PolicyAction / ChangeEventAction / ActorKind
    members. The grep gate inherited from Phases 1-3 MUST still return 1
    match for each Literal declaration site."""

    assert _grep_count(r"^PolicyAction\s*=\s*Literal") == 1
    assert _grep_count(r"^ChangeEventAction\s*=\s*Literal") == 1
    assert _grep_count(r"^ActorKind\s*=\s*Literal") == 1
