"""Derived lexical/vector projections, scoped ranking and integrity manifests.

Candidate indexes never grant access. The shared search service validates current
canonical policy, containment, selected content and offsets before release.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from collections import OrderedDict
from typing import Any

from .encoding_runtime import bounded_setting
from .index_definition import EmbeddingProfile
from .pgvector_driver import _vector_literal


class PassageStore:
    def __init__(self, driver: Any, profile: EmbeddingProfile):
        self.driver, self.profile = driver, profile
        self.table = driver._table + "_passage"
        self.lexical = driver._table + "_lexical"
        self.manifest = driver._table + "_manifest"
        if not all(
            re.fullmatch(r"[a-z][a-z0-9_]{0,62}", n)
            for n in (self.table, self.lexical, self.manifest)
        ):
            raise ValueError("Invalid internal passage table name")
        self._ready = False
        self._selection_indexes_ready = False
        self._lock = asyncio.Lock()
        self.suffix = hashlib.sha256(self.table.encode()).hexdigest()[:12]

    def ann_index_name(self, workspace_id: str) -> str:
        scope = hashlib.sha256(
            (self.profile.id + ":" + workspace_id).encode()
        ).hexdigest()[:20]
        return f"passage_ann_{self.suffix}_{scope}"

    @staticmethod
    def _literal(value: str) -> str:
        # DDL cannot bind predicate constants. Inputs remain escaped literals.
        return "'" + value.replace("'", "''") + "'"

    async def ensure_schema(self) -> Any:
        pool = await self.driver._get_pool()
        if self._ready:
            return pool
        async with self._lock:
            async with pool.acquire() as conn, conn.transaction():
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
                    PRIMARY KEY(entry_id,profile_id,ordinal))"""
                )
                await conn.execute(
                    f"CREATE INDEX IF NOT EXISTS passage_scope_{self.suffix} ON {self.table}(profile_id,workspace_id,app_id,track_id)"
                )
                await conn.execute(
                    f"""CREATE TABLE IF NOT EXISTS {self.lexical} (
                    entry_id text NOT NULL, ordinal integer NOT NULL,
                    track_id text NOT NULL, workspace_id text NOT NULL, app_id text NOT NULL,
                    source_hash text NOT NULL, source_revision bigint NOT NULL,
                    field_path text NOT NULL, start_offset integer NOT NULL, end_offset integer NOT NULL,
                    content text NOT NULL, metadata jsonb NOT NULL DEFAULT '{{}}',
                    search_terms tsvector GENERATED ALWAYS AS (
                        setweight(to_tsvector('simple'::regconfig,content),
                            CASE WHEN field_path='title' THEN 'A'::"char" ELSE 'D'::"char" END)
                    ) STORED, deleted_at timestamptz,
                    PRIMARY KEY(entry_id,ordinal))"""
                )
                await conn.execute(
                    f"CREATE INDEX IF NOT EXISTS passage_text_{self.suffix} ON {self.lexical} USING gin(search_terms) WHERE deleted_at IS NULL"
                )
                await conn.execute(
                    f"CREATE INDEX IF NOT EXISTS passage_text_scope_{self.suffix} ON {self.lexical}(workspace_id,app_id,track_id) WHERE deleted_at IS NULL"
                )
                await conn.execute(
                    f"""CREATE TABLE IF NOT EXISTS {self.manifest} (
                    entry_id text NOT NULL, profile_id text NOT NULL,
                    source_hash text NOT NULL, passage_count integer NOT NULL,
                    checksum text NOT NULL, published_at timestamptz NOT NULL DEFAULT now(),
                    PRIMARY KEY(entry_id,profile_id))"""
                )
            self._ready = True
        return pool

    async def _checksum(self, conn, entry_id: str):
        # A storage-integrity checksum, not an authorization/cryptographic proof.
        return await conn.fetchrow(
            f"""SELECT count(*)::int AS count,
            coalesce(md5(string_agg(jsonb_build_array(ordinal,field_path,start_offset,
                end_offset,content,embedding::text,metadata,source_hash,track_id,
                workspace_id,app_id)::text, E'\\n' ORDER BY ordinal)), '') AS checksum
            FROM {self.table} WHERE entry_id=$1 AND profile_id=$2 AND deleted_at IS NULL""",
            entry_id,
            self.profile.id,
        )

    async def lexical_integrity(self, entry_id: str, source_hash: str) -> bool:
        """Verify the independent text projection without consulting the encoder."""
        pool = await self.ensure_schema()
        async with (
            pool.acquire() as conn,
            conn.transaction(isolation="repeatable_read", readonly=True),
        ):
            expected = await conn.fetchrow(
                f"SELECT * FROM {self.manifest} WHERE entry_id=$1 AND profile_id='lexical'",
                entry_id,
            )
            actual = await self._lexical_checksum(conn, entry_id)
            return bool(
                expected
                and expected["source_hash"] == source_hash
                and expected["passage_count"] == actual["count"]
                and expected["checksum"] == actual["checksum"]
            )

    async def _lexical_checksum(self, conn, entry_id):
        return await conn.fetchrow(
            f"""SELECT count(*)::int AS count,
            coalesce(md5(string_agg(jsonb_build_array(ordinal,field_path,start_offset,
                end_offset,content,metadata,source_hash,track_id,workspace_id,app_id)::text,
                E'\\n' ORDER BY ordinal)), '') AS checksum
            FROM {self.lexical} WHERE entry_id=$1 AND deleted_at IS NULL""",
            entry_id,
        )

    async def integrity(self, entry_id: str, source_hash: str) -> bool:
        """Verify actual stored rows, including every passage, without inference."""
        pool = await self.ensure_schema()
        async with (
            pool.acquire() as conn,
            conn.transaction(isolation="repeatable_read", readonly=True),
        ):
            manifest = await conn.fetchrow(
                f"SELECT * FROM {self.manifest} WHERE entry_id=$1 AND profile_id=$2",
                entry_id,
                self.profile.id,
            )
            if not manifest or manifest["source_hash"] != source_hash:
                return False
            actual = await self._checksum(conn, entry_id)
            valid = (
                actual["count"] == manifest["passage_count"]
                and actual["checksum"] == manifest["checksum"]
            )
        return valid and await self.lexical_integrity(entry_id, source_hash)

    async def verified_sources(self, entry_ids: list[str]) -> dict[str, str]:
        """Verify both independent projections in one bounded storage snapshot."""
        if not entry_ids:
            return {}
        if len(entry_ids) > 1000:
            raise ValueError("Integrity pages are limited to 1,000 entries")
        pool = await self.ensure_schema()
        async with (
            pool.acquire() as conn,
            conn.transaction(isolation="repeatable_read", readonly=True),
        ):
            rows = await conn.fetch(
                f"""WITH vectors AS (
                    SELECT entry_id,count(*)::int AS count,
                    coalesce(md5(string_agg(jsonb_build_array(ordinal,field_path,start_offset,
                        end_offset,content,embedding::text,metadata,source_hash,track_id,
                        workspace_id,app_id)::text, E'\\n' ORDER BY ordinal)), '') AS checksum
                    FROM {self.table} WHERE profile_id=$1 AND entry_id=ANY($2::text[])
                    AND deleted_at IS NULL GROUP BY entry_id
                ), texts AS (
                    SELECT entry_id,count(*)::int AS count,
                    coalesce(md5(string_agg(jsonb_build_array(ordinal,field_path,start_offset,
                        end_offset,content,metadata,source_hash,track_id,workspace_id,app_id)::text,
                        E'\\n' ORDER BY ordinal)), '') AS checksum
                    FROM {self.lexical} WHERE entry_id=ANY($2::text[]) AND deleted_at IS NULL
                    GROUP BY entry_id
                ) SELECT m.entry_id,m.source_hash FROM {self.manifest} m
                JOIN vectors v USING(entry_id)
                JOIN {self.manifest} l ON l.entry_id=m.entry_id AND l.profile_id='lexical'
                JOIN texts t ON t.entry_id=m.entry_id
                WHERE m.profile_id=$1 AND m.passage_count=v.count AND m.checksum=v.checksum
                AND l.source_hash=m.source_hash AND l.passage_count=t.count AND l.checksum=t.checksum""",
                self.profile.id,
                entry_ids,
            )
        return {r["entry_id"]: r["source_hash"] for r in rows}

    async def reusable_vectors(self, entry_id: str) -> dict[str, list[float]]:
        """Reuse only a verified prior publication within this Entry/profile."""
        pool = await self.ensure_schema()
        async with (
            pool.acquire() as conn,
            conn.transaction(isolation="repeatable_read", readonly=True),
        ):
            manifest = await conn.fetchrow(
                f"SELECT * FROM {self.manifest} WHERE entry_id=$1 AND profile_id=$2",
                entry_id,
                self.profile.id,
            )
            actual = await self._checksum(conn, entry_id)
            if (
                not manifest
                or manifest["passage_count"] != actual["count"]
                or manifest["checksum"] != actual["checksum"]
            ):
                return {}
            rows = await conn.fetch(
                f"SELECT content,embedding::text AS vector FROM {self.table} WHERE entry_id=$1 AND profile_id=$2 AND deleted_at IS NULL LIMIT 4096",
                entry_id,
                self.profile.id,
            )
        return {r["content"]: json.loads(r["vector"]) for r in rows}

    def _lexical_rows(self, document, passages):
        return [
            (
                document["entry_id"],
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
                json.dumps(
                    {
                        **document["metadata"],
                        "_integral_type_id": document.get("type_id", ""),
                    }
                ),
            )
            for i, p in enumerate(passages)
        ]

    async def _write_lexical(self, conn, document, passages):
        await conn.execute(
            f"DELETE FROM {self.lexical} WHERE entry_id=$1", document["entry_id"]
        )
        if passages:
            await conn.executemany(
                f"""INSERT INTO {self.lexical}
                (entry_id,ordinal,track_id,workspace_id,app_id,source_hash,source_revision,
                 field_path,start_offset,end_offset,content,metadata)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb)""",
                self._lexical_rows(document, passages),
            )
        actual = await self._lexical_checksum(conn, document["entry_id"])
        await conn.execute(
            f"""INSERT INTO {self.manifest}
            (entry_id,profile_id,source_hash,passage_count,checksum) VALUES ($1,'lexical',$2,$3,$4)
            ON CONFLICT(entry_id,profile_id) DO UPDATE SET source_hash=excluded.source_hash,
            passage_count=excluded.passage_count,checksum=excluded.checksum,published_at=now()""",
            document["entry_id"],
            document["source_hash"],
            actual["count"],
            actual["checksum"],
        )

    async def publish_lexical(self, document, passages, *, guard=None) -> bool:
        """Publish independently of embedding availability, under a fresh guard."""
        pool = await self.ensure_schema()
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))", document["entry_id"]
            )
            if guard is not None and not await guard():
                return False
            await self._write_lexical(conn, document, passages)
        return True

    async def replace(
        self, document: dict, passages: list[dict], vectors: list, *, guard=None
    ) -> bool:
        if len(passages) != len(vectors) or any(
            len(v) != self.profile.dimensions for v in vectors
        ):
            raise ValueError("Passage/vector shape mismatch")
        pool = await self.ensure_schema()
        async with pool.acquire() as conn, conn.transaction():
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
                await conn.executemany(
                    f"""INSERT INTO {self.table}
                    (entry_id,profile_id,ordinal,track_id,workspace_id,app_id,
                     source_hash,source_revision,field_path,start_offset,end_offset,content,metadata,embedding)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13::jsonb,$14::vector)""",
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
                            json.dumps(
                                {
                                    **document["metadata"],
                                    "_integral_type_id": document.get("type_id", ""),
                                }
                            ),
                            _vector_literal(v),
                        )
                        for i, (p, v) in enumerate(zip(passages, vectors))
                    ],
                )
            await self._write_lexical(conn, document, passages)
            actual = await self._checksum(conn, document["entry_id"])
            await conn.execute(
                f"""INSERT INTO {self.manifest}
                (entry_id,profile_id,source_hash,passage_count,checksum) VALUES ($1,$2,$3,$4,$5)
                ON CONFLICT(entry_id,profile_id) DO UPDATE SET source_hash=excluded.source_hash,
                passage_count=excluded.passage_count,checksum=excluded.checksum,published_at=now()""",
                document["entry_id"],
                self.profile.id,
                document["source_hash"],
                actual["count"],
                actual["checksum"],
            )
        return True

    async def maintain_selection_indexes(self) -> None:
        """Build two shared type selectors in worker maintenance, never on reads."""
        if self._selection_indexes_ready:
            return
        pool = await self.ensure_schema()
        async with pool.acquire() as conn:
            lock = self.table + ":selection-indexes"
            if not await conn.fetchval(
                "SELECT pg_try_advisory_lock(hashtext($1))", lock
            ):
                return
            try:
                for name, table, columns in (
                    (
                        "passage_type_" + self.suffix,
                        self.table,
                        "profile_id,workspace_id,(metadata->>'_integral_type_id'),track_id",
                    ),
                    (
                        "passage_lexical_type_" + self.suffix,
                        self.lexical,
                        "workspace_id,(metadata->>'_integral_type_id'),track_id",
                    ),
                ):
                    existing = await conn.fetchrow(
                        "SELECT indisvalid FROM pg_index WHERE indexrelid=to_regclass($1)",
                        name,
                    )
                    if existing and existing["indisvalid"]:
                        continue
                    if existing:
                        await conn.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
                    await conn.execute(
                        f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON {table} ({columns}) WHERE deleted_at IS NULL"
                    )
                self._selection_indexes_ready = True
            finally:
                await conn.execute("SELECT pg_advisory_unlock(hashtext($1))", lock)

    async def maintain_ann(self, workspace_id: str) -> bool:
        """Build qualified large-profile indexes in worker maintenance, never search."""
        if os.getenv("RETRIEVE_ANN_ENABLED", "0") != "1":
            return False
        pool = await self.ensure_schema()
        ann_name = self.ann_index_name(workspace_id)
        threshold = bounded_setting("RETRIEVE_EXACT_THRESHOLD", 2000, 100, 100000)
        async with pool.acquire() as conn:
            count = await conn.fetchval(
                f"SELECT count(*) FROM (SELECT 1 FROM {self.table} WHERE profile_id=$1 AND workspace_id=$2 AND deleted_at IS NULL LIMIT $3) q",
                self.profile.id,
                workspace_id,
                threshold + 1,
            )
            if count <= threshold:
                return False
            existing = await conn.fetchrow(
                "SELECT indisvalid FROM pg_index WHERE indexrelid=to_regclass($1)",
                ann_name,
            )
            if existing and existing["indisvalid"]:
                return True
            locked = await conn.fetchval(
                "SELECT pg_try_advisory_lock(hashtext($1))", self.table + ":ann"
            )
            if not locked:
                return False
            try:
                # A peer may have repaired the index after our initial read.
                existing = await conn.fetchrow(
                    "SELECT indisvalid FROM pg_index WHERE indexrelid=to_regclass($1)",
                    ann_name,
                )
                if existing and existing["indisvalid"]:
                    return True
                if existing:
                    await conn.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {ann_name}")
                return await self._build_ann(conn, ann_name, workspace_id)
            finally:
                await conn.execute(
                    "SELECT pg_advisory_unlock(hashtext($1))", self.table + ":ann"
                )

    async def _build_ann(self, conn, ann_name: str, workspace_id: str) -> bool:
        indexes = await conn.fetchval(
            "SELECT count(*) FROM pg_indexes WHERE tablename=$1 AND indexname LIKE $2",
            self.table,
            f"passage_ann_{self.suffix}_%",
        )
        if indexes >= bounded_setting("RETRIEVE_MAX_ANN_INDEXES", 32, 1, 128):
            return False
        await conn.execute(
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {ann_name} ON {self.table} USING hnsw ((embedding::vector({self.profile.dimensions})) vector_cosine_ops) WHERE profile_id='{self.profile.id}' AND workspace_id={self._literal(workspace_id)} AND deleted_at IS NULL"
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
        eligible_entry_ids: list[str] | None = None,
        type_selection: dict[str, list[str] | None] | None = None,
    ) -> list[dict]:
        """Fuse independently bounded branches; lexical mode needs no vector."""
        if (
            not workspace_id
            or not (track_ids or direct_entry_ids)
            or eligible_entry_ids == []
            or type_selection == {}
        ):
            return []
        if mode != "graph" and len(vector) != self.profile.dimensions:
            raise ValueError("Query vector dimensions differ from embedding profile")
        pool = await self.ensure_schema()
        k = min(max(k, 1), 500)
        where = "workspace_id=$2 AND (track_id=ANY($3::text[]) OR entry_id=ANY($7::text[])) AND ($8::text[] IS NULL OR entry_id=ANY($8::text[])) AND deleted_at IS NULL AND (NOT $9::boolean OR track_id=ANY($10::text[]) OR (metadata->>'_integral_type_id')=ANY($11::text[]))"
        args = [
            self.profile.id,
            workspace_id,
            track_ids,
            _vector_literal(vector or [1.0]),
            k,
            query,
            direct_entry_ids or [],
            eligible_entry_ids,
            type_selection is not None,
            [track for track, types in (type_selection or {}).items() if types is None],
            list(
                {
                    identity
                    for types in (type_selection or {}).values()
                    if types is not None
                    for identity in types
                }
            ),
        ]
        branches = []
        ann_name = self.ann_index_name(workspace_id)
        try:
            async with pool.acquire() as conn, conn.transaction():
                await conn.execute(
                    "SELECT set_config('statement_timeout',$1,true)",
                    str(
                        bounded_setting(
                            "RETRIEVE_STATEMENT_TIMEOUT_MS", 1500, 50, 10000
                        )
                    ),
                )
                if mode != "semantic":
                    lexical = await conn.fetch(
                        f"""SELECT *, 1.0/(60+row_number() OVER (ORDER BY rank DESC,entry_id,ordinal)) AS score FROM (
                        SELECT *,ts_rank_cd(search_terms,websearch_to_tsquery('simple',$6)) AS rank
                        FROM {self.lexical} WHERE {where} AND search_terms @@ websearch_to_tsquery('simple',$6)
                        AND $1::text IS NOT NULL AND $4::text IS NOT NULL
                        ORDER BY rank DESC,entry_id,ordinal LIMIT $5) matches""",
                        *[args[0], args[1], args[2], str(args[3]), *args[4:]],
                    )
                    branches.append(
                        [
                            {**dict(row), "lexical_match": True, "vector_match": False}
                            for row in lexical
                        ]
                    )
                if mode != "graph":
                    # Exact on small admitted scopes. Large scopes may use a managed
                    # partial HNSW index; SQL predicates retain logical admission.
                    scoped = f"profile_id='{self.profile.id}' AND workspace_id={self._literal(workspace_id)} AND {where}"
                    threshold = bounded_setting(
                        "RETRIEVE_EXACT_THRESHOLD", 2000, 100, 100000
                    )
                    size = await conn.fetchval(
                        f"SELECT count(*) FROM (SELECT 1 FROM {self.table} WHERE {scoped} AND $1::text IS NOT NULL AND $4::text IS NOT NULL AND $5::int IS NOT NULL AND $6::text IS NOT NULL LIMIT {threshold + 1}) candidates",
                        *[args[0], args[1], args[2], str(args[3]), *args[4:]],
                    )
                    ready = (
                        os.getenv("RETRIEVE_ANN_ENABLED", "0") == "1"
                        and size > threshold
                        and await conn.fetchval(
                            "SELECT EXISTS(SELECT 1 FROM pg_index i WHERE i.indexrelid=to_regclass($1) AND i.indisvalid)",
                            ann_name,
                        )
                    )
                    if ready:
                        version = await conn.fetchval(
                            "SELECT extversion FROM pg_extension WHERE extname='vector'"
                        )
                        ready = tuple(int(v) for v in version.split(".")[:2]) >= (0, 8)
                    if ready:
                        await conn.execute(
                            "SET LOCAL hnsw.iterative_scan = 'strict_order'"
                        )
                        await conn.execute(
                            "SELECT set_config('hnsw.ef_search',$1,true)",
                            str(
                                max(
                                    k * 2,
                                    bounded_setting(
                                        "RETRIEVE_ANN_EF_SEARCH", 1000, 40, 1000
                                    ),
                                )
                            ),
                        )
                        await conn.execute(
                            "SELECT set_config('hnsw.max_scan_tuples',$1,true)",
                            str(
                                bounded_setting(
                                    "RETRIEVE_ANN_SCAN_LIMIT", 20000, 1000, 100000
                                )
                            ),
                        )
                        source = self.table
                    else:
                        # Exact fallback preserves small/restricted scope recall.
                        source = f"(SELECT * FROM {self.table} WHERE {scoped} OFFSET 0)"
                    dim = self.profile.dimensions
                    rows = await conn.fetch(
                        f"""SELECT *,1.0/(60+row_number() OVER (ORDER BY distance,entry_id,ordinal)) AS score FROM (
                        SELECT *,embedding::vector({dim}) <=> $4::vector({dim}) AS distance
                        FROM {source} p WHERE {scoped} AND $1::text IS NOT NULL AND $6::text IS NOT NULL
                        ORDER BY embedding::vector({dim}) <=> $4::vector({dim}) LIMIT $5) nearest""",
                        *args,
                    )
                    branches.append(
                        [
                            {**dict(row), "lexical_match": False, "vector_match": True}
                            for row in rows
                        ]
                    )
        except Exception as exc:
            if getattr(exc, "sqlstate", None) == "57014":
                from .embedding_store import EmbeddingBusy

                raise EmbeddingBusy("Search ranking timed out; retry shortly") from None
            raise
        merged: dict[tuple, dict[str, Any]] = {}
        for rows in branches:
            for row in rows:
                key = (
                    row["entry_id"],
                    row["field_path"],
                    row["start_offset"],
                    row["end_offset"],
                    row["source_hash"],
                )
                if key in merged:
                    merged[key]["score"] += row["score"]
                    merged[key]["lexical_match"] |= row["lexical_match"]
                    merged[key]["vector_match"] |= row["vector_match"]
                else:
                    merged[key] = row
        return sorted(
            merged.values(),
            key=lambda r: (-r["score"], r["entry_id"], r["start_offset"]),
        )[:k]

    async def soft_delete(self, entry_id: str, *, guard=None) -> bool:
        pool = await self.ensure_schema()
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", entry_id)
            if guard is not None and not await guard():
                return False
            await conn.execute(
                f"UPDATE {self.table} SET deleted_at=now() WHERE entry_id=$1", entry_id
            )
            await conn.execute(
                f"UPDATE {self.lexical} SET deleted_at=now() WHERE entry_id=$1",
                entry_id,
            )
            await conn.execute(
                f"DELETE FROM {self.manifest} WHERE entry_id=$1", entry_id
            )
        return True


_STORES: OrderedDict[tuple, PassageStore] = OrderedDict()


def passage_store(driver: Any, profile: EmbeddingProfile) -> PassageStore:
    """Bound cached adapters; database projections outlive their Python handles."""
    key = (id(driver), profile.id)
    if key not in _STORES:
        _STORES[key] = PassageStore(driver, profile)
    _STORES.move_to_end(key)
    while len(_STORES) > 128:
        _STORES.popitem(last=False)
    return _STORES[key]
