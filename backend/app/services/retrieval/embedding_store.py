"""RET-01 — EmbeddingStore Protocol.

The canonical driver contract for Phase 4 hybrid retrieval. v1 drivers:
``AtlasVectorDriver`` (MongoDB Atlas Vector Search; default on Mongo
deployments) and ``NullEmbeddingStore`` (no-op fallback for graph-only
deployments). The retired ``SqliteVecDriver`` (removed in Wave 3 of the
SaaS-deployment plan) was the original default. Production alternatives
(pgvector, Qdrant, jvspatial-native) plug in via ``register_driver``
without touching call sites.

**Metadata contract.** ``metadata["track_id"]`` is REQUIRED — it powers
the scope pre-filter consumed by Plan 04-02 (CONTEXT lock #6). Drivers
MUST expose ``track_id`` as a queryable index field so the pre-filter
runs at the store level when ``scope="track:<id>"`` (I-RET-04).
``metadata["type_id"]`` is optional.

**Scope param shape (search).**

* ``"track:<id>"`` — driver applies a track-id pre-filter at SQL level
  (perf optimization; saves K candidates from being post-filtered out by
  the per-candidate policy filter in Plan 04-02).
* ``"workspace:<id>"`` — no pre-filter; relies on the per-candidate
  policy filter for permission scoping. The vector store IS NOT a
  parallel system-of-record; the policy filter remains authoritative
  (see ``docs/INVARIANTS.md`` I-RET-01 / I-RET-02).
* ``None`` — universe-wide vector search; policy filter mandatory.

**Soft-delete invariant.** ``soft_delete`` flags the row as
``deleted_at = <ISO timestamp>`` and excludes it from subsequent
``search`` results. Hard-delete is offered only as a separate (rarely-
used) operation — entry-level deletes route through ``soft_delete``
to preserve agent-memory recall and audit-log integrity (I-RET-03).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, Tuple, runtime_checkable


@runtime_checkable
class EmbeddingStore(Protocol):
    """Protocol for pluggable embedding stores."""

    async def upsert(
        self,
        entry_id: str,
        vector: List[float],
        metadata: Dict[str, Any],
    ) -> None:
        """Insert or replace the embedding for ``entry_id``.

        ``metadata["track_id"]`` is REQUIRED — drivers MUST persist it
        on a column / sibling table so it is queryable for the scope
        pre-filter. ``len(vector)`` MUST match the driver's configured
        dimension; mismatches raise ``ValueError``.
        """
        ...

    async def search(
        self,
        vector: List[float],
        k: int = 50,
        scope: Optional[str] = None,
    ) -> List[Tuple[str, float]]:
        """Return up to ``k`` (entry_id, similarity) tuples, sorted
        descending by similarity. Soft-deleted rows MUST be excluded.

        ``scope`` shape and pre-filter semantics: see module docstring.
        """
        ...

    async def soft_delete(self, entry_id: str) -> None:
        """Mark the embedding row as deleted (excluded from ``search``).

        Idempotent — calling on a non-existent / already-soft-deleted row
        MUST NOT raise.
        """
        ...

    async def hard_delete(self, entry_id: str) -> None:
        """Permanently remove the embedding row. Reserved for offline
        backfill / TTL cleanup. **Entry.delete writes route through
        ``soft_delete``, never this.** (I-RET-03)
        """
        ...

    async def count(self) -> int:
        """Return the number of NOT-soft-deleted rows in the store."""
        ...


__all__ = ["EmbeddingStore"]
