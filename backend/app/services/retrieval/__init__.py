"""RET-01 — pluggable embedding-store driver registry.

Registry idiom mirrors Phase 3.1 ``template_var_resolvers``: a
module-level mapping driver-name → instantiated driver, a
``register_driver(name, driver)`` registrar that rejects duplicates, and
a ``get_embedding_store()`` factory keyed on ``EMBEDDING_STORE_DRIVER``.

**Driver resolution.**

* ``EMBEDDING_STORE_DRIVER`` default is ``"auto"``.
* ``auto`` resolves to ``"pgvector"`` when ``JVSPATIAL_DB_TYPE ==
  "postgres"`` AND ``JVSPATIAL_POSTGRES_DSN`` is set (the pgvector driver
  must be registered); to ``"atlas"`` when ``JVSPATIAL_DB_TYPE ==
  "mongodb"`` AND ``JVSPATIAL_MONGODB_URI`` is set; otherwise to
  ``"null"`` — graph-only deployments do not need a vector store, and the
  read path degrades semantic / hybrid → graph transparently.
* Explicit values (``pgvector`` / ``atlas`` / ``null``) override
  resolution.

Auto-registered drivers:

* ``pgvector`` — Postgres + pgvector backend (default on Postgres
  deployments). Registered when ``JVSPATIAL_DB_TYPE == "postgres"`` and
  ``JVSPATIAL_POSTGRES_DSN`` is set; the underlying asyncpg pool is lazily
  opened on first use. Existing entries need a one-time backfill (see
  ``backend/scripts/backfill_embeddings.py``); NEW / updated entries embed
  automatically via the ``_reembed_entry`` write hook.
* ``atlas`` — MongoDB Atlas Vector Search backend (Wave 3 default on Mongo
  deployments). Registered when ``JVSPATIAL_MONGODB_URI`` is set; the
  underlying motor connection is lazily established on first use.
* ``null`` — no-op store used by the ``auto`` fallback path and by every
  graph-only deployment.

``semantic_retrieval_available()`` returns ``True`` iff the *resolved*
driver is a real vector store (i.e. not ``null``). The ``POST
/api/retrieve`` handler consults it to decide whether to honour
``mode=semantic`` / ``mode=hybrid`` or degrade them to ``mode=graph``.
Write hooks (``_reembed_entry``, ``_reembed_synced_entry``) gate on it
to skip the embedding-model load entirely in graph-only deployments.

The import path ``from app.services.retrieval import get_embedding_store``
is locked — downstream code MUST NOT relocate it.
"""

from __future__ import annotations

import logging
import os
from typing import Dict

from .embedding_store import EmbeddingStore

logger = logging.getLogger(__name__)

_REGISTRY: Dict[str, EmbeddingStore] = {}

# Set when Atlas ``$vectorSearch`` / ``$search`` fails at runtime (e.g. local
# Mongo without Atlas Search). Cleared only on process restart — mirrors the
# one-shot degrade warning in ``app.api.retrieve``.
_SEMANTIC_RUNTIME_DISABLED = False


def disable_semantic_retrieval_runtime(reason: str) -> None:
    """Mark semantic retrieval unavailable for the remainder of this process.

    Invoked when the atlas driver is registered (URI present) but the
    deployment cannot execute vector-search aggregation stages — typical
    for docker-compose Mongo without Atlas CLI local search.
    """

    global _SEMANTIC_RUNTIME_DISABLED
    if _SEMANTIC_RUNTIME_DISABLED:
        return
    _SEMANTIC_RUNTIME_DISABLED = True
    logger.warning(
        "retrieval: semantic vector search disabled for remainder of process "
        "(future semantic/hybrid requests degrade to graph): %s",
        reason,
    )


def register_driver(name: str, driver: EmbeddingStore) -> None:
    """Register an EmbeddingStore implementation under ``name``.

    Duplicate registration raises ``ValueError`` — enforces the
    single-source-of-truth invariant (mirror of Phase 1 D-07 + Phase 3.1
    template-var-resolvers registry hygiene). Driver objects MUST satisfy
    the ``EmbeddingStore`` Protocol (duck-typed: upsert / search /
    soft_delete / hard_delete / count).
    """

    if not isinstance(name, str) or not name:
        raise ValueError(f"Driver name must be a non-empty string; got {name!r}")
    if name in _REGISTRY:
        raise ValueError(f"Driver {name!r} already registered")
    _REGISTRY[name] = driver


def _resolve_driver_name() -> str:
    """Resolve ``EMBEDDING_STORE_DRIVER`` to a concrete registered name.

    Default is ``"auto"`` (see module docstring). Explicit values pass
    through unchanged.
    """

    name = os.environ.get("EMBEDDING_STORE_DRIVER", "auto")
    if name != "auto":
        return name
    db_type = (os.environ.get("JVSPATIAL_DB_TYPE") or "").strip().lower()
    if db_type == "postgres":
        # pgvector driver — the Postgres-deployment default. If it is not
        # yet registered (e.g. DSN missing, or asyncpg import failed) we
        # fall back to ``null`` so the deployment still boots; the read
        # path will degrade.
        if "pgvector" in _REGISTRY:
            return "pgvector"
        logger.warning(
            "retrieval: EMBEDDING_STORE_DRIVER=auto + JVSPATIAL_DB_TYPE=postgres "
            "but the 'pgvector' driver is not registered (JVSPATIAL_POSTGRES_DSN "
            "unset, or asyncpg unavailable); falling back to 'null'. Semantic "
            "retrieval will be degraded until the pgvector driver registers."
        )
        return "null"
    if db_type == "mongodb":
        # Atlas Vector Search driver — registered in Wave 3. If it is
        # not yet registered (mid-cutover) we fall back to ``null`` so
        # the deployment still boots; the read path will degrade.
        if "atlas" in _REGISTRY:
            return "atlas"
        logger.warning(
            "retrieval: EMBEDDING_STORE_DRIVER=auto + JVSPATIAL_DB_TYPE=mongodb "
            "but the 'atlas' driver is not registered; falling back to 'null'. "
            "Semantic retrieval will be degraded until the Atlas driver lands."
        )
        return "null"
    return "null"


def get_embedding_store() -> EmbeddingStore:
    """Return the EmbeddingStore selected by ``EMBEDDING_STORE_DRIVER``.

    Unknown driver names raise ``RuntimeError`` — fail-fast (no silent
    fall-through to a misconfigured default).
    """

    name = _resolve_driver_name()
    driver = _REGISTRY.get(name)
    if driver is None:
        raise RuntimeError(
            f"Driver {name!r} not registered " f"(known: {sorted(_REGISTRY.keys())!r})"
        )
    return driver


def semantic_retrieval_available() -> bool:
    """Return True iff the resolved driver backs real semantic retrieval.

    False = the ``null`` driver is active (or the registry resolves to
    one), so ``mode=semantic`` / ``mode=hybrid`` MUST degrade to
    ``mode=graph`` at the dispatch layer and write hooks MUST skip the
    embedding-model load. Read by ``POST /api/retrieve``, by the
    ``_reembed_*`` helpers, and by the eager-load gate in ``main.py``.
    """

    if _SEMANTIC_RUNTIME_DISABLED:
        return False
    try:
        name = _resolve_driver_name()
    except Exception:  # noqa: BLE001 — defensive: env state must never raise
        return False
    if name == "null":
        return False
    return name in _REGISTRY


def get_registered_drivers() -> list[str]:
    """Return the sorted list of currently registered driver names.

    Diagnostic helper — used by tests and the
    ``GET /api/retrieval/config`` surface.
    """

    return sorted(_REGISTRY.keys())


# ---------------------------------------------------------------------------
# Built-in drivers
# ---------------------------------------------------------------------------
#
# ``null`` is always registered — it is the graceful fallback for every
# graph-only deployment and the resolution target when ``auto`` does not
# find a real vector store.
#
# ``atlas`` registers only when ``JVSPATIAL_MONGODB_URI`` is configured.
# The motor connection is lazily opened on first ``upsert`` / ``search``
# call so module import stays cheap and tests / dev that never touch
# semantic retrieval never establish a Mongo client.

from .null_driver import NullEmbeddingStore  # noqa: E402

register_driver("null", NullEmbeddingStore())


# ``pgvector`` registers only on Postgres deployments with a DSN set. The
# asyncpg pool is lazily opened on first ``upsert`` / ``search`` so module
# import stays cheap and graph-only / non-semantic paths never connect.
if (os.environ.get("JVSPATIAL_DB_TYPE") or "").strip().lower() == "postgres" and (
    os.environ.get("JVSPATIAL_POSTGRES_DSN")
):
    try:
        from .pgvector_driver import PgVectorEmbeddingStore

        register_driver("pgvector", PgVectorEmbeddingStore())
    except Exception as exc:  # noqa: BLE001 — broad catch is intentional here.
        logger.warning(
            "retrieval: pgvector driver auto-registration failed "
            "(EMBEDDING_STORE_DRIVER=pgvector will RuntimeError until resolved): %s",
            exc,
        )


if os.environ.get("JVSPATIAL_MONGODB_URI"):
    try:
        from .atlas_vector_driver import AtlasVectorDriver

        register_driver("atlas", AtlasVectorDriver())
    except Exception as exc:  # noqa: BLE001 — broad catch is intentional here.
        logger.warning(
            "retrieval: atlas driver auto-registration failed "
            "(EMBEDDING_STORE_DRIVER=atlas will RuntimeError until resolved): %s",
            exc,
        )


__all__ = [
    "EmbeddingStore",
    "register_driver",
    "get_embedding_store",
    "get_registered_drivers",
    "semantic_retrieval_available",
    "disable_semantic_retrieval_runtime",
]
