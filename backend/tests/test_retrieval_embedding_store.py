"""Regression suite for the retrieval substrate — driver registry, rank
fusion helper, embedding-model deferred import.

The embedding-model import MUST be deferred — the module-load test
asserts neither ``fastembed`` nor the legacy ``sentence_transformers``
chain are pulled into ``sys.modules`` on cold import. (Wave 1 of the
SaaS-deployment plan swapped the runtime from sentence-transformers/torch
to fastembed/onnxruntime; the deferral discipline applies to either
backend.)

The previously-tested ``sqlite-vec`` driver was removed in Wave 3 of the
same plan — its driver-specific behaviours (upsert / search round-trip,
soft-delete exclusion, count semantics, dim mismatch rejection) now live
on the ``AtlasVectorDriver`` integration suite which is skipped unless
``ATLAS_TEST_URI`` is set (Atlas ``$vectorSearch`` cannot be mocked).
"""

from __future__ import annotations

import importlib
import os
import sys
from types import SimpleNamespace

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.model]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reset_retrieval_modules() -> None:
    """Drop the retrieval package from ``sys.modules`` so each test starts
    with a clean driver registry. Mirrors the technique in Phase 3 policy
    engine tests."""

    for mod in list(sys.modules):
        if mod == "app.services.retrieval" or mod.startswith("app.services.retrieval."):
            del sys.modules[mod]


def _fresh_retrieval():
    _reset_retrieval_modules()
    return importlib.import_module("app.services.retrieval")


def _make_vec(seed: int, dim: int = 384) -> list[float]:
    """Deterministic pseudo-random unit-ish vector for tests."""

    import random

    rnd = random.Random(seed)
    return [rnd.uniform(-1.0, 1.0) for _ in range(dim)]


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    # Tests opt in explicitly to driver env settings.
    monkeypatch.delenv("EMBEDDING_STORE_DRIVER", raising=False)
    monkeypatch.delenv("EMBEDDING_STORE_PATH", raising=False)
    monkeypatch.delenv("JVSPATIAL_DB_TYPE", raising=False)
    yield


# ---------------------------------------------------------------------------
# Test 1 — driver registry round-trip
# ---------------------------------------------------------------------------


def test_register_driver_round_trip(monkeypatch):
    retrieval = _fresh_retrieval()

    class FakeStore:
        async def upsert(self, *a, **k):
            return None

        async def search(self, *a, **k):
            return []

        async def soft_delete(self, *a, **k):
            return None

        async def hard_delete(self, *a, **k):
            return None

        async def count(self):
            return 0

    fake = FakeStore()
    retrieval.register_driver("test_driver", fake)
    monkeypatch.setenv("EMBEDDING_STORE_DRIVER", "test_driver")
    assert retrieval.get_embedding_store() is fake


# ---------------------------------------------------------------------------
# Test 2 — duplicate registration rejected
# ---------------------------------------------------------------------------


def test_duplicate_registration_rejected():
    retrieval = _fresh_retrieval()
    # The ``null`` driver auto-registers on module import; registering
    # it again must raise ValueError per the registry hygiene invariant.
    with pytest.raises(ValueError, match="already registered"):
        retrieval.register_driver("null", object())


# ---------------------------------------------------------------------------
# Test 3 — unknown driver name rejected at factory call
# ---------------------------------------------------------------------------


def test_unknown_driver_rejected(monkeypatch):
    retrieval = _fresh_retrieval()
    monkeypatch.setenv("EMBEDDING_STORE_DRIVER", "bogus")
    with pytest.raises(RuntimeError, match="Driver 'bogus' not registered"):
        retrieval.get_embedding_store()


# ---------------------------------------------------------------------------
# Tests 4–7 — driver-specific round-trip / soft-delete / count / dim-mismatch
# previously exercised the ``sqlite-vec`` driver. Wave 3 of the
# SaaS-deployment plan removed that driver; the equivalent contract is now
# verified against the ``AtlasVectorDriver`` in
# ``tests/test_retrieval_atlas_driver.py`` (marked ``@pytest.mark.atlas``;
# skipped unless ``ATLAS_TEST_URI`` is set — Atlas ``$vectorSearch`` cannot
# be mocked, so no in-process equivalent exists).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Test 8 — RRF combines multiple rankings
# ---------------------------------------------------------------------------


def test_rrf_combines_rankings():
    from app.services.retrieval.rank_fusion import reciprocal_rank_fusion

    rankings = [
        [("a", 1.0), ("b", 0.9)],
        [("b", 1.0), ("a", 0.9)],
    ]
    fused = reciprocal_rank_fusion(rankings, k=60, top_n=20)
    fused_ids = [row[0] for row in fused]
    # Both 'a' and 'b' appear once at rank-1 and once at rank-2 — RRF
    # scores tie; insertion order from sorted-by-score breaks the tie
    # deterministically. Both must be present.
    assert set(fused_ids) == {"a", "b"}


def test_rrf_orders_by_total_score():
    from app.services.retrieval.rank_fusion import reciprocal_rank_fusion

    # 'a' appears top of both rankings — wins.
    rankings = [
        [("a", 1.0), ("b", 0.9), ("c", 0.8)],
        [("a", 1.0), ("c", 0.9), ("b", 0.8)],
    ]
    fused = reciprocal_rank_fusion(rankings, k=60, top_n=20)
    assert fused[0][0] == "a"


# ---------------------------------------------------------------------------
# Test 9 — RRF top_n cap
# ---------------------------------------------------------------------------


def test_rrf_top_n_cap():
    from app.services.retrieval.rank_fusion import reciprocal_rank_fusion

    ranking = [(f"id{i}", 1.0 - i * 0.01) for i in range(50)]
    fused = reciprocal_rank_fusion([ranking], k=60, top_n=5)
    assert len(fused) == 5


# ---------------------------------------------------------------------------
# Test 10 — embed_entry_text drops empty parts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embed_entry_text_drops_empty_parts(monkeypatch):
    """embed_entry_text composes only non-empty title/body/string-valued
    custom_fields. The embedding model.encode is monkeypatched to return a
    deterministic vector keyed on the text it received, so we can assert the
    text composition without downloading the real model."""

    from app.services.retrieval import embedding_model as em

    captured = {}

    class FakeModel:
        def encode(self, text, convert_to_numpy=False):
            captured["text"] = text
            return [float(len(text))] * em.EMBEDDING_DIM

    monkeypatch.setattr(em, "_MODEL", FakeModel())

    entry = SimpleNamespace(title="", body="x", custom_fields={})
    vec = await em.embed_entry_text(entry)
    assert isinstance(vec, list)
    assert len(vec) == em.EMBEDDING_DIM
    # Title was empty → must NOT be included; body 'x' must be the only part.
    assert captured["text"].strip() == "x"


# ---------------------------------------------------------------------------
# Test 11 — embed_entry_text skips non-string custom_fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embed_entry_text_skips_non_string_custom_fields(monkeypatch):
    from app.services.retrieval import embedding_model as em

    captured = {}

    class FakeModel:
        def encode(self, text, convert_to_numpy=False):
            captured["text"] = text
            return [0.0] * em.EMBEDDING_DIM

    monkeypatch.setattr(em, "_MODEL", FakeModel())

    entry = SimpleNamespace(
        title="T",
        body="B",
        custom_fields={"date": "2026-01-01", "count": 5, "flag": True},
    )
    await em.embed_entry_text(entry)
    # 'count' (int) and 'flag' (bool) MUST be skipped; '2026-01-01' MUST be present.
    assert "2026-01-01" in captured["text"]
    assert "5" not in captured["text"]
    assert "True" not in captured["text"]


# ---------------------------------------------------------------------------
# Test 12 — embedding-runtime import is deferred
# ---------------------------------------------------------------------------


def test_embedding_runtime_import_deferred():
    """Module-load of embedding_model MUST NOT pull the embedding runtime
    into sys.modules — keeps the heavy native chain off the cold app path
    (RESEARCH Pitfall 5).

    Covers the current ``fastembed`` backend (onnxruntime + tokenizers)
    AND the retired ``sentence_transformers`` backend (torch + transformers),
    so the test stays meaningful if either backend is reinstated.
    """

    heavy = ("fastembed", "sentence_transformers", "onnxruntime", "torch")

    # Drop any cached versions first.
    for mod in list(sys.modules):
        for prefix in heavy:
            if mod == prefix or mod.startswith(prefix + "."):
                del sys.modules[mod]
                break
    if "app.services.retrieval.embedding_model" in sys.modules:
        del sys.modules["app.services.retrieval.embedding_model"]

    importlib.import_module("app.services.retrieval.embedding_model")
    for prefix in heavy:
        assert prefix not in sys.modules, (
            f"embedding_model module-load must not import {prefix!r} "
            f"(deferral discipline, RESEARCH Pitfall 5)"
        )


# ---------------------------------------------------------------------------
# Wave 2 — null driver + auto resolution + semantic_retrieval_available
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_null_driver_is_noop():
    """NullEmbeddingStore upsert/soft_delete/hard_delete return None; search → []; count → 0."""
    from app.services.retrieval.null_driver import NullEmbeddingStore

    drv = NullEmbeddingStore()
    await drv.upsert("e1", [0.0] * 384, {"track_id": "T"})  # MUST NOT raise
    await drv.soft_delete("e1")
    await drv.hard_delete("e1")
    assert await drv.search([0.0] * 384, k=10) == []
    assert await drv.count() == 0


def test_auto_resolves_to_null_on_sqlite(monkeypatch):
    """``auto`` (default) + JVSPATIAL_DB_TYPE!=mongodb → ``null`` resolution."""
    retrieval = _fresh_retrieval()
    monkeypatch.setenv("JVSPATIAL_DB_TYPE", "sqlite")
    # auto is the default — explicit set covers operators who pin it.
    monkeypatch.setenv("EMBEDDING_STORE_DRIVER", "auto")
    store = retrieval.get_embedding_store()
    assert store.__class__.__name__ == "NullEmbeddingStore"


def test_auto_resolves_to_null_when_atlas_unregistered(monkeypatch):
    """``auto`` + JVSPATIAL_DB_TYPE=mongodb but no atlas driver → null fallback."""
    retrieval = _fresh_retrieval()
    monkeypatch.setenv("JVSPATIAL_DB_TYPE", "mongodb")
    monkeypatch.setenv("EMBEDDING_STORE_DRIVER", "auto")
    # The atlas driver lands in Wave 3 — until then auto falls back to null
    # rather than 500-ing the boot path.
    store = retrieval.get_embedding_store()
    assert store.__class__.__name__ == "NullEmbeddingStore"


def test_default_driver_is_auto(monkeypatch):
    """No env set → default ``auto`` resolution kicks in."""
    retrieval = _fresh_retrieval()
    monkeypatch.delenv("EMBEDDING_STORE_DRIVER", raising=False)
    # Should not raise; resolves to null on sqlite / non-mongo.
    store = retrieval.get_embedding_store()
    assert store is not None


def test_semantic_retrieval_available_false_on_null(monkeypatch):
    """``semantic_retrieval_available()`` returns False when null is active."""
    retrieval = _fresh_retrieval()
    monkeypatch.setenv("EMBEDDING_STORE_DRIVER", "null")
    assert retrieval.semantic_retrieval_available() is False


def test_semantic_retrieval_available_true_for_non_null_driver(monkeypatch):
    """``semantic_retrieval_available()`` returns True for any non-null driver."""
    retrieval = _fresh_retrieval()

    class _FakeStore:
        async def upsert(self, *a, **k):
            return None

        async def search(self, *a, **k):
            return []

        async def soft_delete(self, *a, **k):
            return None

        async def hard_delete(self, *a, **k):
            return None

        async def count(self):
            return 0

    retrieval.register_driver("not_null", _FakeStore())
    monkeypatch.setenv("EMBEDDING_STORE_DRIVER", "not_null")
    assert retrieval.semantic_retrieval_available() is True


def test_null_driver_auto_registered():
    """``null`` is auto-registered as the always-available fallback."""
    retrieval = _fresh_retrieval()
    assert "null" in retrieval.get_registered_drivers()
