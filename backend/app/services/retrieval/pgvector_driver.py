"""PgVectorEmbeddingStore — Postgres + pgvector embedding store.

Postgres-deployment counterpart to ``AtlasVectorDriver``. Where Atlas
backs semantic / hybrid retrieval with MongoDB Atlas Vector Search, this
driver backs it with the ``vector`` extension (pgvector) on the same
Postgres cluster Integral already stores its graph in. Like the atlas
driver it does NOT route writes through jvspatial Node CRUD — embedding
records are a derived side-index owned by this driver, not a graph
participant (I-GRAPH-02). It opens its OWN lazy asyncpg pool from
``JVSPATIAL_POSTGRES_DSN`` (decoupled from jvspatial's pool — mirrors how
the atlas driver opens its own motor client).

Table shape (one row per Entry)::

    CREATE TABLE entry_embedding (
      entry_id    text PRIMARY KEY,
      track_id    text NOT NULL,
      type_id     text,
      vector      vector(384) NOT NULL,
      deleted_at  timestamptz,
      updated_at  timestamptz NOT NULL DEFAULT now()
    );

``track_id`` is a queryable column (B-tree indexed) powering the
``scope="track:<id>"`` pre-filter (I-RET-04). An HNSW index over
``vector vector_cosine_ops`` accelerates the cosine ANN search.

``search`` runs ``ORDER BY vector <=> $query LIMIT k`` with cosine
distance, returning ``1 - distance`` as the similarity score, and always
excludes soft-deleted rows (``deleted_at IS NULL``). The per-candidate
policy filter in ``backend/app/api/retrieve.py`` STILL runs on every
survivor — this store is a candidate generator, NOT a parallel
system-of-record (I-RET-01 / I-RET-02). The ``scope`` param is only a
perf pre-filter:

* ``"track:<id>"`` — adds ``AND track_id = <id>`` to the WHERE clause.
* ``"workspace:<id>"`` / ``None`` — no pre-filter; the policy filter
  remains authoritative downstream.

``soft_delete`` stamps ``deleted_at = now()`` (idempotent — no row → no
error); the search WHERE clause then naturally excludes the row. A
subsequent ``upsert`` clears ``deleted_at`` (re-embed un-deletes).
``hard_delete`` removes the row outright (reserved for offline backfill /
TTL — entry deletes route through ``soft_delete`` per I-RET-03).

``ensure_schema`` is idempotent (``CREATE EXTENSION/TABLE/INDEX IF NOT
EXISTS``) and runs at most once per process, guarded by an asyncio lock so
concurrent first-callers do not race the DDL.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


EMBEDDING_DIM = 384
DEFAULT_TABLE = "entry_embedding"


def _vector_literal(vector: List[float]) -> str:
    """Render a python float list as a pgvector text literal ``'[v1,v2,...]'``.

    pgvector accepts this textual form for both binds and inserts, so we
    avoid registering a binary codec (portable across asyncpg / pgbouncer
    setups). ``repr``-free float formatting keeps the payload compact.
    """

    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


class PgVectorEmbeddingStore:
    """Postgres + pgvector embedding store."""

    driver_name = "pgvector"

    def __init__(
        self,
        *,
        dsn: Optional[str] = None,
        table: str = DEFAULT_TABLE,
        dim: int = EMBEDDING_DIM,
    ) -> None:
        self._dsn = dsn or os.environ.get("JVSPATIAL_POSTGRES_DSN", "")
        self._table = table
        self._dim = dim
        self._pool: Any = None
        self._schema_ready = False
        # Guards both pool creation and the one-shot ensure_schema DDL so
        # concurrent first-callers do not race.
        self._lock = asyncio.Lock()

        if not self._dsn:
            raise RuntimeError(
                "PgVectorEmbeddingStore requires JVSPATIAL_POSTGRES_DSN (or an "
                "explicit dsn argument); none was provided"
            )

    # ------------------------------------------------------------------
    # Internal — pool + schema lazy bootstrap
    # ------------------------------------------------------------------

    async def _get_pool(self) -> Any:
        if self._pool is not None and self._schema_ready:
            return self._pool
        async with self._lock:
            if self._pool is None:
                # Deferred import — keep asyncpg off the module-load path
                # so import stays cheap in deployments that never touch
                # semantic retrieval.
                import asyncpg

                self._pool = await asyncpg.create_pool(
                    self._dsn, min_size=1, max_size=5
                )
            if not self._schema_ready:
                await self._ensure_schema(self._pool)
                self._schema_ready = True
        return self._pool

    async def _ensure_schema(self, pool: Any) -> None:
        """Idempotently provision the extension, table, and indexes."""

        # HNSW index name is derived from the table so a test-scoped table
        # gets its own (distinct) index names without colliding.
        track_idx = f"{self._table}_track_idx"
        hnsw_idx = f"{self._table}_hnsw"
        async with pool.acquire() as conn:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            await conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self._table} (
                  entry_id    text PRIMARY KEY,
                  track_id    text NOT NULL,
                  type_id     text,
                  vector      vector({self._dim}) NOT NULL,
                  deleted_at  timestamptz,
                  updated_at  timestamptz NOT NULL DEFAULT now()
                )
                """
            )
            await conn.execute(
                f"CREATE INDEX IF NOT EXISTS {track_idx} "
                f"ON {self._table} (track_id)"
            )
            await conn.execute(
                f"CREATE INDEX IF NOT EXISTS {hnsw_idx} ON {self._table} "
                f"USING hnsw (vector vector_cosine_ops)"
            )

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

        vec_literal = _vector_literal(vector)
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {self._table}
                    (entry_id, track_id, type_id, vector, deleted_at, updated_at)
                VALUES ($1, $2, $3, $4::vector, NULL, now())
                ON CONFLICT (entry_id) DO UPDATE SET
                    track_id   = EXCLUDED.track_id,
                    type_id    = EXCLUDED.type_id,
                    vector     = EXCLUDED.vector,
                    deleted_at = NULL,
                    updated_at = now()
                """,
                str(entry_id),
                str(track_id),
                type_id,
                vec_literal,
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

        vec_literal = _vector_literal(vector)
        params: List[Any] = [vec_literal, k]
        where = "deleted_at IS NULL"
        # CONTEXT lock #6 — track scope adds an SQL pre-filter; workspace /
        # None scopes rely on the authoritative downstream policy filter.
        if scope and scope.startswith("track:"):
            track_id = scope.split(":", 1)[1]
            where += " AND track_id = $3"
            params.append(track_id)

        sql = (
            f"SELECT entry_id, 1 - (vector <=> $1::vector) AS sim "
            f"FROM {self._table} "
            f"WHERE {where} "
            f"ORDER BY vector <=> $1::vector "
            f"LIMIT $2"
        )
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [(str(r["entry_id"]), float(r["sim"])) for r in rows]

    async def soft_delete(self, entry_id: str) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            # Idempotent — a non-existent / already-soft-deleted row simply
            # matches zero/one rows; UPDATE never raises on no match.
            await conn.execute(
                f"UPDATE {self._table} SET deleted_at = now() WHERE entry_id = $1",
                str(entry_id),
            )

    async def hard_delete(self, entry_id: str) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                f"DELETE FROM {self._table} WHERE entry_id = $1",
                str(entry_id),
            )

    async def count(self) -> int:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            value = await conn.fetchval(
                f"SELECT count(*) FROM {self._table} WHERE deleted_at IS NULL"
            )
        return int(value or 0)

    # ------------------------------------------------------------------
    # Provisioning / lifecycle
    # ------------------------------------------------------------------

    async def ensure_schema(self) -> None:
        """Public idempotent schema provisioner (mirrors atlas's ensure_search_index).

        Forces the lazy pool + DDL bootstrap. Safe to call repeatedly.
        """
        await self._get_pool()

    async def close(self) -> None:
        """Close the asyncpg pool (test teardown / graceful shutdown)."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            self._schema_ready = False


__all__ = [
    "PgVectorEmbeddingStore",
    "EMBEDDING_DIM",
    "DEFAULT_TABLE",
]
