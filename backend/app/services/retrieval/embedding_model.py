"""RET-02 — eager-loaded ONNX embedding singleton + embed helper.

Wave 1 swap (SaaS-deployment plan, Option B): the embedding runtime moved
from ``sentence-transformers`` (torch + transformers, ~1–2 GB on disk
plus a heavy cold-import) to ``fastembed`` (ONNX Runtime). The model
itself is unchanged — ``sentence-transformers/all-MiniLM-L6-v2``, 384-dim
output — only the inference backend differs, so existing 384-dim vectors
remain dimension-compatible.

The ``fastembed`` import still pulls a non-trivial native stack
(onnxruntime + tokenizers), so RESEARCH Pitfall 5 still applies:

1. Module-load MUST NOT import ``fastembed`` — the import is deferred
   to inside ``load_model()``. ``test_retrieval_embedding_store.py``
   Test 12 asserts the deferral.
2. ``load_model()`` is idempotent and the singleton is held in a
   module-level ``_MODEL`` variable. App startup calls it eagerly behind
   ``EMBEDDING_MODEL_EAGER_LOAD=1`` (default ON; tests flip it OFF).
3. ``embed_entry_text()`` / ``embed_query_text()`` run the encode inside
   ``asyncio.to_thread`` so the event loop stays responsive.
4. The singleton exposes a stable ``encode(text, convert_to_numpy=False)``
   surface via a thin ``_FastembedAdapter`` — so existing test fakes that
   monkeypatch ``_MODEL`` with that same shape keep working.

**Embed input shape (CONTEXT lock #4 — "no-tag-only-reembed"):**
``title + body + string-valued custom_fields``. Tags are projected at
retrieval time via filter, not embedded — so tag mutations skip re-embed
entirely (enforced at the call site in ``app/api/entries.py``).
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 384
DEFAULT_MODEL_NAME = os.environ.get(
    "EMBEDDING_MODEL_NAME", "sentence-transformers/all-MiniLM-L6-v2"
)

# Singleton held module-level. Public read-only (tests monkeypatch directly
# to inject a stub model without touching the network).
_MODEL: Optional[Any] = None


class _FastembedAdapter:
    """Thin compatibility layer over ``fastembed.TextEmbedding``.

    Exposes ``encode(text, convert_to_numpy=False)`` so callers (and test
    fakes) keep the same surface that ``sentence-transformers`` used.
    Internally it delegates to ``TextEmbedding.embed([text])`` and pulls
    the first (only) vector out of the resulting generator.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def encode(self, text: str, convert_to_numpy: bool = False) -> Any:
        # fastembed.embed accepts an iterable of strings and yields a
        # numpy ndarray per input. One input → one vector.
        gen = self._inner.embed([text])
        vec = next(iter(gen))
        if convert_to_numpy:
            return vec
        # Cheaper to hand a list of floats over the asyncio boundary
        # than a numpy array; matches the prior sentence-transformers
        # convert_to_numpy=False contract.
        try:
            return [float(x) for x in vec.tolist()]
        except AttributeError:
            return [float(x) for x in vec]


def load_model() -> Any:
    """Idempotent eager loader for the embedding model singleton.

    Called by ``backend/app/main.py`` at startup when
    ``EMBEDDING_MODEL_EAGER_LOAD=1`` (default ON). Returns the cached
    model on subsequent calls. The ``fastembed`` import is intentionally
    inside the function body so module-load stays cheap (RESEARCH Pitfall
    5). First call downloads the ONNX weights to ``FASTEMBED_CACHE_PATH``
    if they aren't already present; production images pre-bake the cache
    in the Docker build to avoid runtime fetches (see backend/Dockerfile).
    """

    global _MODEL
    if _MODEL is not None:
        return _MODEL

    # Deferred import — onnxruntime + tokenizers chain stays off the
    # cold app-startup path until this function is actually called.
    from fastembed import TextEmbedding  # noqa: WPS433

    logger.info("retrieval: loading embedding model %s", DEFAULT_MODEL_NAME)
    inner = TextEmbedding(model_name=DEFAULT_MODEL_NAME)
    _MODEL = _FastembedAdapter(inner)
    return _MODEL


def _compose_entry_text(entry: Any) -> str:
    """Compose the embed input string from an Entry-shaped object.

    Pulls ``title``, ``body``, and string-valued ``custom_fields``
    (CONTEXT lock #4). Non-string custom_fields are dropped because they
    don't carry retrieval-meaningful signal (numbers, booleans, dates as
    timestamps tend to add noise; the date-string form is preserved if
    the caller stored it as a string).

    Result is capped at 2000 chars to keep the encode call cheap. The
    parts are joined with " | " so the model can see the field
    boundaries.
    """

    parts: list[str] = []
    title = getattr(entry, "title", None) or ""
    body = getattr(entry, "body", None) or ""
    if title.strip():
        parts.append(title)
    if body.strip():
        parts.append(body)
    custom = getattr(entry, "custom_fields", None) or {}
    if isinstance(custom, dict):
        for _key, value in custom.items():
            # Tag mutations DO NOT route here — they hit a different
            # endpoint that skips re-embed entirely (CONTEXT lock #4).
            # Booleans subclass int in Python, so isinstance check has
            # to exclude bool explicitly.
            if isinstance(value, bool):
                continue
            if isinstance(value, str) and value.strip():
                parts.append(value)
    return " | ".join(parts)[:2000]


async def embed_entry_text(entry: Any) -> list[float]:
    """Embed ``title + body + string-valued custom_fields`` for ``entry``.

    Returns a python list of floats with length ``EMBEDDING_DIM``. The
    actual encode runs inside ``asyncio.to_thread`` so the event loop
    stays responsive (the ONNX encode is sync).
    """

    text = _compose_entry_text(entry)
    model = load_model()

    def _encode() -> Any:
        return model.encode(text, convert_to_numpy=False)

    raw = await asyncio.to_thread(_encode)
    if isinstance(raw, list):
        return [float(x) for x in raw]
    # Defensive: callers (or test stubs) may return numpy regardless.
    try:
        return [float(x) for x in raw.tolist()]
    except AttributeError:
        return [float(x) for x in raw]


async def embed_query_text(query: str) -> list[float]:
    """Embed a free-text query string for retrieval (Plan 04-02 consumer).

    Mirrors ``embed_entry_text`` but accepts a raw string rather than a
    structured Entry. The string is stripped + capped at 2000 chars to
    keep the encode call cheap (same cap as ``_compose_entry_text``).

    Returns a python list of floats with length ``EMBEDDING_DIM``. The
    actual encode runs inside ``asyncio.to_thread`` so the event loop
    stays responsive.
    """

    text = (query or "").strip()[:2000]
    model = load_model()

    def _encode() -> Any:
        return model.encode(text, convert_to_numpy=False)

    raw = await asyncio.to_thread(_encode)
    if isinstance(raw, list):
        return [float(x) for x in raw]
    try:
        return [float(x) for x in raw.tolist()]
    except AttributeError:
        return [float(x) for x in raw]


__all__ = [
    "EMBEDDING_DIM",
    "DEFAULT_MODEL_NAME",
    "load_model",
    "embed_entry_text",
    "embed_query_text",
]
