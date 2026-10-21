"""RET-02 — lazily imported, bounded ONNX text encoder singleton.

Uses the same MiniLM ONNX model, tokenizer, mean pooling and normalization as
before, without FastEmbed's unrelated Pillow upper bound. Heavy inference stays
in worker threads and cold initialization is serialized. Public callers retain
encode(text, convert_to_numpy=False); tests may inject that same model shape.
The container prefetches pinned artifacts through load_model().

**Embed input shape (CONTEXT lock #4 — "no-tag-only-reembed"):**
``title + body + string-valued custom_fields``. Tags are projected at
retrieval time via filter, not embedded — so tag mutations skip re-embed
entirely (enforced at the call site in ``app/api/entries.py``).
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import threading
from typing import Any, Optional

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 384
DEFAULT_MODEL_NAME = os.environ.get(
    "EMBEDDING_MODEL_NAME", "sentence-transformers/all-MiniLM-L6-v2"
)

# Singleton held module-level. Public read-only (tests monkeypatch directly
# to inject a stub model without touching the network).
_MODEL: Optional[Any] = None
_MODEL_LOCK = threading.RLock()


def _load_model_unlocked() -> Any:
    """Load pinned model artifacts once, with heavy imports deferred."""

    global _MODEL
    if _MODEL is not None:
        return _MODEL

    # Deferred imports keep the ONNX/tokenizer stack off the cold boot path.
    from .onnx_text_encoder import OnnxTextEncoder, encoder_artifact

    logger.info("retrieval: loading embedding model %s", DEFAULT_MODEL_NAME)
    threads = min(16, max(1, int(os.getenv("EMBEDDING_MODEL_THREADS", "1"))))
    _MODEL = OnnxTextEncoder(encoder_artifact(DEFAULT_MODEL_NAME), threads)
    return _MODEL


def load_model() -> Any:
    """Serialize cold initialization, including callers from worker threads."""
    with _MODEL_LOCK:
        return _load_model_unlocked()


async def _passage_model() -> Any:
    from .embedding_store import EmbeddingStoreUnavailable

    try:
        return await asyncio.to_thread(load_model)
    except Exception as exc:
        raise EmbeddingStoreUnavailable("The search model is unavailable") from exc


async def passage_tokenizer() -> Any:
    """Independent untruncated tokenizer; never mutate inference's tokenizer."""
    from tokenizers import Tokenizer

    model = await _passage_model()
    tokenizer = model.tokenizer
    clone = Tokenizer.from_str(tokenizer.to_str())
    clone.no_truncation()
    clone.no_padding()
    return clone


async def encode_passages(texts: list[str], dimensions: int) -> list[list[float]]:
    """Bounded batch encoding with explicit model/dimension validation."""
    model = await _passage_model()

    def encode() -> list[list[float]]:
        # Inference truncates at the model's limit. Refuse oversized passages
        # instead of silently losing their tail, including a misconfigured chunk.
        from tokenizers import Tokenizer

        source = model.tokenizer
        tokenizer = Tokenizer.from_str(source.to_str())
        tokenizer.no_padding()
        tokenizer.no_truncation()
        maximum = (source.truncation or {}).get("max_length", 512)
        from .embedding_store import EmbeddingStoreUnavailable

        if (
            int(os.getenv("INDEX_CHUNK_TOKENS", "160"))
            + tokenizer.num_special_tokens_to_add(False)
            > maximum
        ):
            raise EmbeddingStoreUnavailable(
                "The configured passage size exceeds the search model limit"
            )
        if any(len(tokenizer.encode(text).ids) > maximum for text in texts):
            raise ValueError("Text exceeds this embedding model's token limit")
        vectors = [
            [float(x) for x in vector] for vector in model.embed(texts, batch_size=16)
        ]
        if len(vectors) != len(texts) or any(
            len(v) != dimensions or not all(math.isfinite(x) for x in v)
            for v in vectors
        ):
            raise EmbeddingStoreUnavailable(
                "Configured embedding dimensions do not match the model"
            )
        return vectors

    def serialized_encode():
        with _MODEL_LOCK:
            return encode()

    return await asyncio.to_thread(serialized_encode)


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
    model = await asyncio.to_thread(load_model)

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
    model = await asyncio.to_thread(load_model)

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
