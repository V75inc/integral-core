"""Version-isolated pgvector passages. Candidate selection is not authority."""

from __future__ import annotations

import asyncio
import hashlib
import re
from typing import Any

from .index_definition import EmbeddingProfile
from .pgvector_driver import _vector_literal


class PassageStore:
    def __init__(self, driver: Any, profile: EmbeddingProfile):
        self.driver, self.profile = driver, profile
        self.table = driver._table + "_passage"
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,54}", self.table):
            raise ValueError("Invalid internal passage table name")
        self._ready = False
        self._lock = asyncio.Lock()

    async def ensure_schema(self) -> Any:
        pool = await self.driver._get_pool()
        if self._ready:
            return pool
        async with self._lock:
            async with pool.acquire() as conn, conn.transaction():
                # Coordinate DDL across workers, not just tasks in this process.
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))", self.table
                )
                await conn.execute(
                    f"""CREATE TABLE IF NOT EXISTS {self.table} (
                      entry_id text NOT NULL, profile_id text NOT NULL,
                      ordinal integer NOT NULL, track_id text NOT NULL,
                      workspace_id text NOT NULL, app_id text NOT NULL,
                      source_hash text NOT NULL, source_revision bigint NOT NULL,
                      field_path text NOT NULL, start_offset integer NOT NULL,
                      end_offset integer NOT NULL, content text NOT NULL,
                      metadata jsonb NOT NULL DEFAULT '{{}}',
                      embedding vector NOT NULL, deleted_at timestamptz,
                      PRIMARY KEY(entry_id, profile_id, ordinal))"""
                )
                suffix = hashlib.sha256(self.table.encode()).hexdigest()[:12]
                await conn.execute(
                    f"CREATE INDEX IF NOT EXISTS passage_scope_{suffix} "
                    f"ON {self.table}(profile_id, workspace_id, app_id, track_id)"
                )
                # Each model/dimension space has its own index and predicate.
                # Profile ids are server-generated SHA256, never caller SQL.
                profile = self.profile
                await conn.execute(
                    f"CREATE INDEX IF NOT EXISTS passage_ann_{suffix}_{profile.id[:16]} "
                    f"ON {self.table} USING hnsw "
                    f"((embedding::vector({profile.dimensions})) vector_cosine_ops) "
                    f"WHERE profile_id = '{profile.id}' AND deleted_at IS NULL"
                )
            self._ready = True
        return pool

    async def replace(
        self, document: dict, passages: list[dict], vectors: list, *, guard=None
    ) -> bool:
        if len(passages) != len(vectors) or any(
            len(v) != self.profile.dimensions for v in vectors
        ):
            raise ValueError("Passage/vector shape mismatch")
        pool = await self.ensure_schema()
        async with pool.acquire() as conn, conn.transaction():
            # Fence concurrent old/new workers on one Entry. Hash validation at
            # read time remains mandatory even after this atomic replacement.
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))", document["entry_id"]
            )
            if guard is not None and not await guard():
                return False
            await conn.execute(
                f"DELETE FROM {self.table} WHERE entry_id=$1 AND profile_id=$2",
                document["entry_id"],
                self.profile.id,
            )
            if passages:
                import json

                await conn.executemany(
                    f"""INSERT INTO {self.table}
                    (entry_id,profile_id,ordinal,track_id,workspace_id,app_id,
                     source_hash,source_revision,field_path,start_offset,end_offset,
                     content,metadata,embedding) VALUES
                    ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13::jsonb,$14::vector)""",
                    [
                        (
                            document["entry_id"],
                            self.profile.id,
                            i,
                            document["track_id"],
                            document["workspace_id"],
                            document["app_id"],
                            document["source_hash"],
                            document["source_revision"],
                            p["field"],
                            p["start"],
                            p["end"],
                            p["text"],
                            json.dumps(document["metadata"]),
                            _vector_literal(v),
                        )
                        for i, (p, v) in enumerate(zip(passages, vectors))
                    ],
                )

        return True

    async def search(
        self,
        vector: list[float],
        *,
        workspace_id: str,
        track_ids: list[str],
        k: int,
        query: str = "",
        mode: str = "semantic",
        direct_entry_ids: list[str] | None = None,
    ) -> list[dict]:
        """Rank only the authorized scope's current model space.

        Use exact search over the narrowed candidate set: no global ANN cap can
        starve a small tenant or Track. The same SQL supports lexical and hybrid
        fusion without hydrating every Entry or sending its body to the agent.
        """
        if not workspace_id or not (track_ids or direct_entry_ids):
            return []
        if len(vector) != self.profile.dimensions:
            raise ValueError("Query vector dimensions differ from embedding profile")
        pool = await self.ensure_schema()
        async with pool.acquire() as conn:
            # MATERIALIZED keeps exact per-scope recall independent of HNSW's
            # global candidate budget. ANN can be enabled after scoped load proof.
            rows = await conn.fetch(
                f"""WITH scoped AS MATERIALIZED (
                  SELECT * FROM {self.table} WHERE profile_id=$1
                  AND workspace_id=$2 AND (track_id=ANY($3::text[]) OR entry_id=ANY($8::text[]))
                  AND deleted_at IS NULL
                ), ranked AS (
                  SELECT *, row_number() OVER (ORDER BY embedding <=> $4::vector,
                  entry_id,ordinal) AS semantic_rank,
                  row_number() OVER (ORDER BY ts_rank_cd(
                    to_tsvector('simple',content),websearch_to_tsquery('simple',$6))
                    DESC,entry_id,ordinal) AS lexical_rank,
                  to_tsvector('simple',content) @@
                    websearch_to_tsquery('simple',$6) AS lexical_match
                  FROM scoped
                ) SELECT *, CASE WHEN $7='graph' THEN
                    CASE WHEN lexical_match THEN 1.0/(60+lexical_rank) ELSE 0 END
                  WHEN $7='hybrid' THEN 1.0/(60+semantic_rank) +
                    CASE WHEN lexical_match THEN 1.0/(60+lexical_rank) ELSE 0 END
                  ELSE 1.0/(60+semantic_rank) END AS score
                  FROM ranked WHERE $7<>'graph' OR lexical_match
                  ORDER BY score DESC, entry_id,ordinal LIMIT $5""",
                self.profile.id,
                workspace_id,
                track_ids,
                _vector_literal(vector),
                min(max(k, 1), 500),
                query,
                mode,
                direct_entry_ids or [],
            )
        return [dict(row) for row in rows]

    async def soft_delete(self, entry_id: str, *, guard=None) -> bool:
        pool = await self.ensure_schema()
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", entry_id)
            if guard is not None and not await guard():
                return False
            # All profile spaces, so a future profile switch cannot revive it.
            await conn.execute(
                f"UPDATE {self.table} SET deleted_at=now() WHERE entry_id=$1",
                entry_id,
            )

        return True


_STORES: dict[tuple, PassageStore] = {}


def passage_store(driver: Any, profile: EmbeddingProfile) -> PassageStore:
    """Reuse the store for this driver and isolated model space."""
    key = (id(driver), profile.id)
    if key not in _STORES:
        _STORES[key] = PassageStore(driver, profile)
    return _STORES[key]
