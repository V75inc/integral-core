"""NullEmbeddingStore — no-op driver for graph-only deployments.

Wave 2 of the SaaS-deployment plan (Option D fallback path). When the
prime database is not MongoDB there is no Atlas Vector Search to back
semantic / hybrid retrieval; the registry then resolves the
``EMBEDDING_STORE_DRIVER`` to ``null`` and every write hook becomes a
cheap no-op. The corresponding read path (``POST /api/retrieve``)
inspects ``semantic_retrieval_available()`` and degrades semantic /
hybrid requests to ``mode=graph`` (``degraded=true``).

Contract:

* ``upsert`` / ``soft_delete`` / ``hard_delete`` — no-op; the call still
  succeeds so write-side callers do not need to branch on availability.
* ``search`` — returns ``[]``. Callers that reach ``search`` while the
  null driver is active are a wiring bug (the dispatch layer should have
  short-circuited to graph) — the empty result is safe but not the
  intended path.
* ``count`` — returns ``0``.

The class is intentionally tiny and has no I/O, so it satisfies the
``EmbeddingStore`` Protocol via duck typing without dragging in any
dependency.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


class NullEmbeddingStore:
    """No-op embedding store for graph-only deployments."""

    async def upsert(
        self,
        entry_id: str,
        vector: List[float],
        metadata: Dict[str, Any],
    ) -> None:
        return None

    async def search(
        self,
        vector: List[float],
        k: int = 50,
        scope: Optional[str] = None,
    ) -> List[Tuple[str, float]]:
        return []

    async def soft_delete(self, entry_id: str) -> None:
        return None

    async def hard_delete(self, entry_id: str) -> None:
        return None

    async def count(self) -> int:
        return 0


__all__ = ["NullEmbeddingStore"]
