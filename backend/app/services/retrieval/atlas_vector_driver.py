"""AtlasVectorDriver — MongoDB Atlas Vector Search embedding store.

Wave 3 of the SaaS-deployment plan (Option D). Replaces the per-instance
``sqlite-vec`` file with a shared Atlas Vector Search index next to the
data Integral already stores in MongoDB. The driver writes to a dedicated
``entry_embeddings`` collection in the prime Mongo database (the one
jvspatial uses); it does NOT route writes through jvspatial Node CRUD,
because embedding records are not graph participants (I-GRAPH-02 — they
are a derived side-index owned by this driver, not Node or Object).

Document shape (one per Entry):

    {
      "_id": "<entry_id>",
      "vector": [384 floats],
      "track_id": "<track_id>",
      "type_id": "<type_id or null>",
      "deleted": false,
      "deleted_at": null,
      "updated_at": "<ISO 8601>"
    }

Atlas Vector Search index (created idempotently by ``ensure_search_index``):

    {
      "name": "entry_embedding_vector_idx",
      "type": "vectorSearch",
      "fields": [
        {"type": "vector", "path": "vector", "numDimensions": 384,
         "similarity": "cosine"},
        {"type": "filter", "path": "track_id"},
        {"type": "filter", "path": "deleted"}
      ]
    }

``search`` issues a ``$vectorSearch`` aggregation with ``numCandidates ≈
10 × k``, ``limit = k``, and a filter that always excludes
``deleted=true`` plus an optional ``track_id`` equality when ``scope`` is
``"track:<id>"``. The per-candidate policy filter in
``backend/app/api/retrieve.py`` STILL runs on every survivor — Atlas is
a candidate generator, not a parallel system-of-record (I-RET-01 /
I-RET-02).

``soft_delete`` flips ``deleted=true`` and stamps ``deleted_at``; the
``$vectorSearch`` filter then naturally excludes the row. ``hard_delete``
is reserved for offline backfill / TTL — entry-level deletes route
through ``soft_delete`` (I-RET-03).

``ensure_search_index`` is idempotent: it lists existing search indexes
on the collection and creates the canonical definition only if absent.
Atlas builds search indexes asynchronously; the call returns immediately
once the create request is accepted (Atlas may still be hydrating).
Deployments lacking ``createSearchIndex`` privileges should provision the
index manually via the Atlas UI / CLI per ``docs/ops/DEPLOY.md``.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


EMBEDDING_DIM = 384
DEFAULT_COLLECTION = "entry_embeddings"
DEFAULT_INDEX_NAME = "entry_embedding_vector_idx"

# Atlas convention: numCandidates >= ~10× limit gives ANN recall close to
# brute-force without paying the full cost. The official guidance is
# >=10×, with diminishing returns above ~20×.
NUM_CANDIDATES_MULTIPLIER = 10


class AtlasVectorDriver:
    """MongoDB Atlas Vector Search embedding store."""

    driver_name = "atlas_vector"

    def __init__(
        self,
        *,
        uri: Optional[str] = None,
        db_name: Optional[str] = None,
        collection: str = DEFAULT_COLLECTION,
        index_name: str = DEFAULT_INDEX_NAME,
        dim: int = EMBEDDING_DIM,
    ) -> None:
        self._uri = uri or os.environ.get("JVSPATIAL_MONGODB_URI", "")
        self._db_name = db_name or os.environ.get(
            "JVSPATIAL_MONGODB_DB_NAME", "integral"
        )
        self._collection_name = collection
        self._index_name = index_name
        self._dim = dim
        self._client: Any = None
        self._collection: Any = None

        if not self._uri:
            raise RuntimeError(
                "AtlasVectorDriver requires JVSPATIAL_MONGODB_URI (or an explicit "
                "uri argument); none was provided"
            )

    # ------------------------------------------------------------------
    # Internal — connection lazy bootstrap
    # ------------------------------------------------------------------

    def _connect(self) -> Any:
        if self._collection is not None:
            return self._collection
        # Deferred import — motor pulls pymongo + asyncio plumbing; we
        # only want to pay that cost when the driver is actually used.
        from motor.motor_asyncio import AsyncIOMotorClient

        self._client = AsyncIOMotorClient(self._uri)
        self._collection = self._client[self._db_name][self._collection_name]
        return self._collection

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    # ------------------------------------------------------------------
    # EmbeddingStore Protocol
    # ------------------------------------------------------------------

    async def upsert(
        self,
        entry_id: str,
        vector: List[float],
        metadata: Dict[str, Any],
    ) -> None:
        if len(vector) != self._dim:
            raise ValueError(f"dim mismatch: expected {self._dim}, got {len(vector)}")
        track_id = (metadata or {}).get("track_id")
        if not track_id:
            raise ValueError("metadata['track_id'] is required for upsert")
        type_id = (metadata or {}).get("type_id") or None

        doc = {
            "vector": [float(x) for x in vector],
            "track_id": str(track_id),
            "type_id": type_id,
            "deleted": False,
            "deleted_at": None,
            "updated_at": self._now_iso(),
        }
        coll = self._connect()
        await coll.update_one(
            {"_id": entry_id},
            {"$set": doc},
            upsert=True,
        )

    async def search(
        self,
        vector: List[float],
        k: int = 50,
        scope: Optional[str] = None,
    ) -> List[Tuple[str, float]]:
        if len(vector) != self._dim:
            raise ValueError(f"dim mismatch: expected {self._dim}, got {len(vector)}")
        if k <= 0:
            return []
        filter_clauses: List[Dict[str, Any]] = [{"deleted": {"$eq": False}}]
        if scope and scope.startswith("track:"):
            track_id = scope.split(":", 1)[1]
            filter_clauses.append({"track_id": {"$eq": track_id}})

        # $vectorSearch ``filter`` accepts a single MQL-style document.
        filter_doc: Dict[str, Any] = (
            filter_clauses[0] if len(filter_clauses) == 1 else {"$and": filter_clauses}
        )

        pipeline = [
            {
                "$vectorSearch": {
                    "index": self._index_name,
                    "path": "vector",
                    "queryVector": [float(x) for x in vector],
                    "numCandidates": max(k * NUM_CANDIDATES_MULTIPLIER, k),
                    "limit": k,
                    "filter": filter_doc,
                }
            },
            {
                "$project": {
                    "_id": 1,
                    "score": {"$meta": "vectorSearchScore"},
                }
            },
        ]
        coll = self._connect()
        results: List[Tuple[str, float]] = []
        async for doc in coll.aggregate(pipeline):
            results.append((str(doc["_id"]), float(doc.get("score") or 0.0)))
        return results

    async def soft_delete(self, entry_id: str) -> None:
        coll = self._connect()
        # Idempotent — update_one with a non-existent _id is a no-op.
        await coll.update_one(
            {"_id": entry_id},
            {"$set": {"deleted": True, "deleted_at": self._now_iso()}},
        )

    async def hard_delete(self, entry_id: str) -> None:
        coll = self._connect()
        await coll.delete_one({"_id": entry_id})

    async def count(self) -> int:
        coll = self._connect()
        return int(await coll.count_documents({"deleted": {"$ne": True}}))

    # ------------------------------------------------------------------
    # Provisioning
    # ------------------------------------------------------------------

    async def ensure_search_index(self) -> None:
        """Create ``entry_embedding_vector_idx`` if it does not exist.

        Atlas Vector Search indexes are created asynchronously; this
        method only verifies the create request is accepted. Operators
        can fall back to manual provisioning via the Atlas UI / CLI per
        ``docs/ops/DEPLOY.md``.
        """
        coll = self._connect()
        definition = {
            "name": self._index_name,
            "type": "vectorSearch",
            "definition": {
                "fields": [
                    {
                        "type": "vector",
                        "path": "vector",
                        "numDimensions": self._dim,
                        "similarity": "cosine",
                    },
                    {"type": "filter", "path": "track_id"},
                    {"type": "filter", "path": "deleted"},
                ]
            },
        }
        try:
            existing: List[Dict[str, Any]] = []
            async for entry in coll.list_search_indexes():
                existing.append(entry)
            if any(entry.get("name") == self._index_name for entry in existing):
                logger.info(
                    "retrieval.atlas: search index %s already exists on %s.%s",
                    self._index_name,
                    self._db_name,
                    self._collection_name,
                )
                return
            await coll.create_search_index(definition)
            logger.info(
                "retrieval.atlas: requested creation of search index %s on %s.%s "
                "(Atlas builds asynchronously)",
                self._index_name,
                self._db_name,
                self._collection_name,
            )
        except Exception as exc:  # noqa: BLE001
            # Common in self-hosted Mongo / lacking Atlas privileges —
            # log and continue; operators are expected to provision
            # manually in that case.
            logger.warning(
                "retrieval.atlas: ensure_search_index failed (manual "
                "provisioning per DEPLOY.md may be required): %s",
                exc,
            )


__all__ = [
    "AtlasVectorDriver",
    "EMBEDDING_DIM",
    "DEFAULT_COLLECTION",
    "DEFAULT_INDEX_NAME",
]
