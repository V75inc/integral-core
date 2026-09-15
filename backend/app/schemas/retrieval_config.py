"""Schema for /api/retrieval/config — Phase 8 SET-06 read-only surface.

Wire shape returned by ``GET /api/retrieval/config`` (Plan 08-04 Task 1).
Read-only in v1.1 per locked decision A1 — no PATCH route, no SystemConfig
node, no write surface. The endpoint exposes the three Phase 4 env-resolved
defaults plus the embedding-store backend identity for operator visibility.

Per Phase 8 threat model T-08-04-I01: ``extra='forbid'`` blocks future
drift that would otherwise leak embedding-store internals (model paths,
vector dimensions, credentials) — the response shape is locked to a
boolean + two ints + one short backend identifier string. Nothing else.
"""

from __future__ import annotations

from pydantic import BaseModel


class RetrievalConfigResponse(BaseModel):
    """Read-only retrieval-config response.

    Mirrors the three env vars read by ``backend/app/api/retrieve.py`` at
    call time (``EMBEDDING_MODEL_EAGER_LOAD`` / ``RETRIEVE_K_DEFAULT`` /
    ``RETRIEVE_TOP_N_DEFAULT``) plus a short string identifier for the
    currently-registered embedding-store driver (e.g. ``"AtlasVectorDriver"``)
    and a ``semantic_available`` flag for operator visibility into the
    graceful-fallback state (Wave 2 of the SaaS-deployment plan).

    Fields:
        embedding_model_eager_load: True when the embedding model is
            warmed at process startup (env ``EMBEDDING_MODEL_EAGER_LOAD``,
            default ``"1"`` → True).
        retrieve_k_default: Default ``k`` over-fetch count for hybrid
            retrieval (env ``RETRIEVE_K_DEFAULT``, default ``150``).
        retrieve_top_n_default: Default ``top_n`` truncation for retrieval
            results (env ``RETRIEVE_TOP_N_DEFAULT``, default ``20``).
        embedding_store_backend: Short identifier of the registered
            embedding-store driver — either ``driver_name`` attribute (if
            present) or the class name (e.g. ``"AtlasVectorDriver"``).
            Returns ``"unknown"`` when no driver is registered.
        semantic_available: True when the resolved driver backs real
            semantic retrieval. False when the ``null`` driver is active,
            in which case ``POST /api/retrieve`` degrades semantic /
            hybrid requests to ``mode=graph`` and surfaces
            ``degraded=true`` in the response.
    """

    embedding_model_eager_load: bool
    retrieve_k_default: int
    retrieve_top_n_default: int
    embedding_store_backend: str
    semantic_available: bool

    model_config = {"extra": "forbid"}
