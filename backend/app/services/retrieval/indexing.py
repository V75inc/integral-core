"""Durable derived-index work, reconciliation and current-source validation.

Job records contain identities/hashes, not copied user content. Entry commands
retain intent in their graph transaction. Bounded reconciliation also covers
imports, older data and schema/location changes that bypass an HTTP handler.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from typing import Any

from jvspatial.core.context import get_default_context

from .index_definition import (
    IndexDefinition,
    digest,
    field_types,
    selected_text,
    split_passages,
)

logger = logging.getLogger(__name__)
ENTITY = "EntryIndexJob"
CURSOR = "o.EntryIndexCursor.v1"


async def entry_index_definition(
    entry: Any, track_id: str, *, schemas=None
) -> IndexDefinition | None:
    """Resolve current selection without hydrating any source text or files."""
    from app.models.nodes import EntryType, Track

    if getattr(entry, "status", "") in {"archived", "deleted"}:
        return None
    schemas = {} if schemas is None else schemas
    if track_id not in schemas:
        schemas[track_id] = await Track.find_one({"id": track_id})
    track = schemas[track_id]
    if track is None:
        return None
    declaration = IndexDefinition.model_validate(track.search_index or {})
    fields = declaration.model_dump()
    if entry.type_id and entry.type_id not in schemas:
        schemas[entry.type_id] = await EntryType.find_one({"id": entry.type_id})
    et = schemas.get(entry.type_id)
    if et and (et.form_schema or {}).get("search_index") is not None:
        override = IndexDefinition.model_validate(et.form_schema["search_index"])
        fields["enabled"] = declaration.enabled and override.enabled
        allowed = []
        for path in override.fields:
            if path in declaration.fields or (
                path.startswith("custom_fields.")
                and "custom_fields.*" in declaration.fields
            ):
                allowed.append(path)
            elif path == "custom_fields.*":
                allowed.extend(
                    p for p in declaration.fields if p.startswith("custom_fields.")
                )
        fields["fields"] = list(dict.fromkeys(allowed))
        fields["exclude_fields"] = list(
            dict.fromkeys(declaration.exclude_fields + override.exclude_fields)
        )
        if override.entry_type_ids and entry.type_id not in override.entry_type_ids:
            return None
        if "metadata_fields" in override.model_fields_set:
            fields["metadata_fields"] = [
                p for p in override.metadata_fields if p in declaration.metadata_fields
            ]
        levels = ["none", "metadata", "content"]
        fields["attachments"] = levels[
            min(
                levels.index(declaration.attachments),
                levels.index(override.attachments),
            )
        ]

    # Both declarations are validated. Their bounded exclusion union may be
    # larger than one declaration's input limit; composition only narrows it.
    declaration = declaration.model_copy(update=fields)
    if not declaration.enabled or (
        declaration.entry_type_ids and entry.type_id not in declaration.entry_type_ids
    ):
        return None
    return declaration


async def index_document(entry: Any, *, location=None, schemas=None) -> dict | None:
    """Compose selected current canonical content and its source fingerprint."""
    from app.services.resource_location import DATA_APP_STATES, locate_resource

    location = location or await locate_resource(
        "entry", entry.id, allowed_app_states=DATA_APP_STATES
    )
    if location is None or getattr(entry, "status", "") in {"archived", "deleted"}:
        return None
    schemas = {} if schemas is None else schemas
    declaration = await entry_index_definition(
        entry, location.track_id, schemas=schemas
    )
    if declaration is None:
        return None
    entry_type = schemas.get(entry.type_id)
    types = field_types(getattr(entry_type, "form_schema", None))
    texts = selected_text(entry, declaration, types=types)
    from .attachment_sources import attachment_sources

    attachment_text, passage_sources = await attachment_sources(
        entry, declaration.attachments
    )
    texts.update(attachment_text)
    if not texts:
        return None
    metadata = {
        path: (entry.custom_fields or {}).get(path.removeprefix("custom_fields."))
        for path in declaration.metadata_fields
        if path.startswith("custom_fields.")
        and path not in declaration.exclude_fields
        and "custom_fields.*" not in declaration.exclude_fields
        and types.get(path.removeprefix("custom_fields."))
        not in {"password", "secret", "relation", "file", "files"}
    }
    metadata = {
        k: v
        for k, v in metadata.items()
        if isinstance(v, (str, int, float, bool)) and len(str(v)) <= 512
    }
    doc = {
        "entry_id": entry.id,
        "type_id": entry.type_id or "",
        "track_id": location.track_id,
        "workspace_id": location.workspace_id,
        "app_id": location.app_id or "",
        "source_revision": getattr(entry, "record_revision", 1),
        "title": entry.title or "",
        "texts": texts,
        "metadata": metadata,
        "definition": declaration.model_dump(),
        "passage_sources": passage_sources,
    }
    # Revision is returned from the current record, not trusted from the index.
    # Tag-only/nonselected edits need no fresh inference; filters are live reads.
    fingerprint = {
        k: v for k, v in doc.items() if k not in {"source_revision", "type_id"}
    }
    # An additive file adapter must not invalidate every attachment-free Entry.
    # Selection is already reflected in texts/sources; preserve their existing
    # fingerprint when there is no file evidence instead of rebuilding a corpus.
    fingerprint["definition"] = {
        k: v for k, v in doc["definition"].items() if k != "attachments"
    }
    if not passage_sources:
        fingerprint.pop("passage_sources")
    doc["source_hash"] = digest(fingerprint)
    return doc


async def enqueue_entry(
    entry: Any,
    *,
    force: bool = False,
    source_error: str = "",
    verify_integrity: bool = False,
) -> None:
    """Persist invalidation without doing embedding/network work on a write."""
    from . import get_embedding_store
    from .pgvector_driver import PgVectorEmbeddingStore

    if not isinstance(get_embedding_store(), PgVectorEmbeddingStore):
        return
    doc = None
    if not source_error:
        try:
            doc = await index_document(entry)
        except Exception as exc:
            source_error = type(exc).__name__
    await _enqueue_document(
        entry,
        doc,
        force=force,
        source_error=source_error,
        verify_integrity=verify_integrity,
    )


async def _enqueue_document(
    entry, doc, *, force=False, source_error="", verify_integrity=False
):
    """Retain prepared reconciliation facts as intent, never as publication authority."""
    from app.services.entry_write_scope import entry_write_scope

    from .embedding_configuration import indexing_profiles
    from .migration_barrier import barrier

    workspace_id = doc["workspace_id"] if doc else ""
    if doc is None:
        from app.services.resource_location import (
            MANAGEMENT_APP_STATES,
            locate_resource,
        )

        # Excluded/archived/deleted content still needs a workspace-scoped
        # invalidation. Entry.workspace_id is only a legacy optional cache;
        # deriving this from it loses soft-deletes when that cache is empty.
        location = await locate_resource(
            "entry", entry.id, allowed_app_states=MANAGEMENT_APP_STATES
        )
        if location is None and getattr(entry, "track_id", ""):
            location = await locate_resource(
                "track", entry.track_id, allowed_app_states=MANAGEMENT_APP_STATES
            )
        if location is not None:
            workspace_id = location.workspace_id
    async with entry_write_scope(workspace_id):
        await barrier(workspace_id, lock=True)
        try:
            profiles = await indexing_profiles(workspace_id)
        except (ValueError, RuntimeError):
            profiles = [None]
            source_error = "InvalidEmbeddingProfile"
        for profile in profiles:
            await _enqueue_profile_document(
                entry,
                doc,
                profile=profile,
                workspace_id=workspace_id,
                force=force,
                source_error=source_error,
                verify_integrity=verify_integrity,
            )


async def _enqueue_profile_document(
    entry,
    doc,
    *,
    profile,
    workspace_id,
    force=False,
    source_error="",
    verify_integrity=False,
):
    from . import get_embedding_store
    from .migration_barrier import advance

    database = get_default_context().database
    profile_id = profile.id if profile is not None else "unconfigured"
    identity = "o.EntryIndexJob." + digest([entry.id, profile_id])
    source_hash = (
        ("unresolved:" + source_error)
        if source_error
        else (doc["source_hash"] if doc else "excluded")
    )
    existing = await database.get("object", identity)
    if existing and int(existing["context"].get("source_revision", 0)) > int(
        getattr(entry, "record_revision", 1)
    ):
        return
    if existing and existing["context"].get("workspace_id") not in {
        None,
        "",
        workspace_id,
    }:
        from app.services.resource_location import locate_resource

        location = await locate_resource("entry", entry.id)
        if location is None or location.workspace_id != workspace_id:
            return
    if (
        existing
        and not force
        and existing["context"].get("source_hash") == source_hash
        and existing["context"].get("projection_version") == 2
        and existing["context"].get("type_id", "") == getattr(entry, "type_id", "")
    ):
        data = existing["context"]
        if data.get("status") in {"pending", "working"}:
            return
        if data.get("status") == "lexical_only":
            from .embedding_configuration import encoding_authorized

            if not await encoding_authorized(profile):
                return
        # A matching receipt is not proof that the complete projection exists.
        # Reconciliation repairs lost/corrupt rows even without a source edit.
        from .passage_store import passage_store

        if data.get("status") == "indexed" and (
            not verify_integrity
            or doc is None
            or await passage_store(get_embedding_store(), profile).integrity(
                entry.id, source_hash
            )
        ):
            return
    context = {
        "entry_id": entry.id,
        "workspace_id": workspace_id,
        "profile_id": profile_id,
        "projection_version": 2,
        "type_id": getattr(entry, "type_id", "") or "",
        "source_hash": source_hash,
        "source_revision": int(getattr(entry, "record_revision", 1)),
        "intent_epoch": await advance(workspace_id),
        "error": source_error,
        "track_id": entry.track_id,
        "status": "pending",
        "attempt": 0,
        "force_encode": force,
        "due": time.time(),
        "generation": uuid.uuid4().hex,
    }
    row = {"id": identity, "entity": ENTITY, "context": context}
    for _ in range(8):
        if existing is None:
            if await database.insert_if_absent("object", row):
                return
        elif (
            await database.find_one_and_update(
                "object",
                {
                    "id": identity,
                    "context.generation": existing["context"].get("generation"),
                },
                {"$set": {"context": context}},
            )
            is not None
        ):
            return
        existing = await database.get("object", identity)
    # Canonical commands roll back rather than acknowledge a lost invalidation.
    raise RuntimeError("Concurrent search indexing updates; retry the change")


async def enqueue_record(record: dict, *, force: bool = False) -> None:
    """Retain per-record failures without blocking the rest of a sweep."""
    from types import SimpleNamespace

    from app.models.nodes import Entry

    try:
        entry = await Entry.find_one({"id": record["id"]})
    except Exception as exc:
        entry = SimpleNamespace(
            id=record["id"], track_id=(record.get("context") or {}).get("track_id", "")
        )
        await enqueue_entry(entry, force=force, source_error=type(exc).__name__)
        return
    if entry:
        await enqueue_entry(entry, force=force, verify_integrity=True)


async def reconcile_page(*, limit: int = 100) -> int:
    """Persist a bounded keyset checkpoint; repeat sweeps catch every mutation."""
    database = get_default_context().database
    cursor = await database.get("object", CURSOR)
    after = (cursor or {}).get("context", {}).get("after", "")
    predicate = {"entity": "Entry", "id": {"$gt": after}}
    rows = await database.find("node", predicate, sort=[("id", 1)], limit=limit)
    from app.models.nodes import Entry
    from app.services.resource_location import DATA_APP_STATES, locate_resources

    from . import get_embedding_store
    from .pgvector_driver import PgVectorEmbeddingStore

    locations: dict[str, Any] | None = None
    schemas: dict[str, Any] = {}
    if rows and isinstance(get_embedding_store(), PgVectorEmbeddingStore):
        try:
            locations = await locate_resources(
                "entry",
                [r["id"] for r in rows],
                selected_records=rows,
                allowed_app_states=DATA_APP_STATES,
            )
        except Exception:
            # Preserve the existing per-record quarantine path if one malformed
            # source prevents a complete bounded graph projection.
            locations = None
    for row in rows:
        if locations is None:
            await enqueue_record(row)
            continue
        try:
            entry = await get_default_context()._deserialize_entity(Entry, row)
            location = locations.get(row["id"])
            document = (
                await index_document(entry, location=location, schemas=schemas)
                if location
                else None
            )
            # These are fresh page facts for job intent only. Delivery rechecks
            # source/profile/ownership before egress and under its publish lock.
            await _enqueue_document(entry, document, verify_integrity=True)
        except Exception:
            await enqueue_record(row)

    await database.save(
        "object",
        {
            "id": CURSOR,
            "entity": "EntryIndexCursor",
            "context": {"after": rows[-1]["id"] if len(rows) == limit else ""},
        },
    )
    return len(rows)


async def deliver_index_jobs(*, limit: int = 16) -> dict:
    """Deliver retryable jobs with cross-worker lease and source fencing."""
    from app.models.nodes import Entry

    from . import get_embedding_store
    from .embedding_configuration import encoding_authorized, job_profile
    from .embedding_model import passage_tokenizer
    from .passage_store import passage_store
    from .pgvector_driver import PgVectorEmbeddingStore
    from .profile_encoder import encode_profile

    driver = get_embedding_store()
    stats = {"indexed": 0, "failed": 0, "superseded": 0}
    if not isinstance(driver, PgVectorEmbeddingStore):
        return stats
    database = get_default_context().database
    now = time.time()
    rows = await database.find(
        "object",
        {
            "entity": ENTITY,
            "$or": [
                {"context.status": "pending", "context.due": {"$lte": now}},
                {"context.status": "working", "context.lease_until": {"$lte": now}},
            ],
        },
        limit=limit,
    )
    from collections import defaultdict, deque

    grouped: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
    for row in rows:
        grouped[row["context"].get("workspace_id", "")].append(row)
    fair = []
    while grouped:
        for workspace in list(grouped):
            fair.append(grouped[workspace].popleft())
            if not grouped[workspace]:
                del grouped[workspace]
    tokenizer = None
    for row in fair:
        ctx = row["context"]
        lease = uuid.uuid4().hex
        claimed = await database.find_one_and_update(
            "object",
            {
                "id": row["id"],
                "context.generation": ctx["generation"],
                "context.status": ctx["status"],
                **(
                    {"context.lease": ctx.get("lease")}
                    if ctx["status"] == "working"
                    else {}
                ),
            },
            {
                "$set": {
                    "context.status": "working",
                    "context.lease": lease,
                    "context.lease_until": time.time() + 300,
                }
            },
        )
        if claimed is None:
            continue
        fence = {
            "id": row["id"],
            "context.generation": ctx["generation"],
            "context.lease": lease,
        }
        try:
            entry = await Entry.find_one({"id": ctx["entry_id"]})
            document = await index_document(entry) if entry else None
            workspace_id = (
                document["workspace_id"] if document else ctx.get("workspace_id", "")
            )
            profile = await job_profile(workspace_id, ctx["profile_id"])
            if profile is None:
                await database.find_one_and_update(
                    "object", fence, {"$set": {"context.status": "superseded"}}
                )
                if entry:
                    await enqueue_entry(entry)
                stats["superseded"] += 1
                continue
            store = passage_store(driver, profile)

            async def publishable(
                row=row,
                ctx=ctx,
                lease=lease,
                document=document,
                workspace_id=workspace_id,
                profile=profile,
            ):
                # Run UNDER the passage transaction's cross-worker entry lock.
                # A replaced lease/generation cannot overwrite a newer delivery.
                job = await database.get("object", row["id"])
                data = (job or {}).get("context", {})
                if (
                    data.get("status") != "working"
                    or data.get("generation") != ctx["generation"]
                    or data.get("lease") != lease
                    or data.get("lease_until", 0) <= time.time()
                ):
                    return False
                current = await job_profile(workspace_id, profile.id)
                if current is None:
                    return False
                fresh = await Entry.find_one({"id": ctx["entry_id"]})
                latest = await index_document(fresh) if fresh else None
                return (latest["source_hash"] if latest else "excluded") == (
                    document["source_hash"] if document else "excluded"
                ) and (latest.get("type_id", "") if latest else "") == (
                    document.get("type_id", "") if document else ""
                )

            if document is None:
                published = await store.soft_delete(ctx["entry_id"], guard=publishable)
            else:
                if tokenizer is None:
                    tokenizer = await passage_tokenizer()
                passages = split_passages(document["texts"], profile, tokenizer)
                if not await store.publish_lexical(
                    document, passages, guard=publishable
                ):
                    stats["superseded"] += 1
                    continue
                if not await encoding_authorized(profile):
                    # Keyword freshness is independent of permission to fund a
                    # provider. No inference is dispatched for a retired/revoked
                    # model, and this receipt never claims vector readiness.
                    await database.find_one_and_update(
                        "object",
                        fence,
                        {
                            "$set": {
                                "context.status": "lexical_only",
                                "context.error": "EmbeddingUnavailable",
                                "context.source_hash": document["source_hash"],
                                "context.type_id": document.get("type_id", ""),
                                "context.projection_version": 2,
                            }
                        },
                    )
                    stats["failed"] += 1
                    continue
                reused = (
                    {}
                    if ctx.get("force_encode")
                    else await store.reusable_vectors(entry.id)
                )
                missing = [p for p in passages if p["text"] not in reused]
                lost_lease = False
                for start in range(0, len(missing), 32):
                    # Recheck source selection and egress authority before each
                    # physical batch; publication checks cannot recall sent text.
                    if not await publishable():
                        lost_lease = True
                        break
                    batch = missing[start : start + 32]
                    vectors = await encode_profile([p["text"] for p in batch], profile)
                    renewed = await database.find_one_and_update(
                        "object",
                        {**fence, "context.lease_until": {"$gt": time.time()}},
                        {"$set": {"context.lease_until": time.time() + 300}},
                    )
                    if renewed is None:
                        lost_lease = True
                        break
                    reused.update({p["text"]: v for p, v in zip(batch, vectors)})
                if lost_lease:
                    stats["superseded"] += 1
                    continue
                published = await store.replace(
                    document,
                    passages,
                    [reused[p["text"]] for p in passages],
                    guard=publishable,
                )
            if not published:
                await database.find_one_and_update(
                    "object",
                    fence,
                    {
                        "$set": {
                            "context.status": "pending",
                            "context.due": time.time(),
                        }
                    },
                )
                stats["superseded"] += 1
                continue
            updated = await database.find_one_and_update(
                "object",
                fence,
                {
                    "$set": {
                        "context.status": "indexed",
                        "context.indexed_at": time.time(),
                        "context.source_hash": (
                            document["source_hash"] if document else "excluded"
                        ),
                        "context.indexed_hash": (
                            document["source_hash"] if document else "excluded"
                        ),
                        "context.error": "",
                        "context.projection_version": 2,
                        "context.source_revision": (
                            document["source_revision"]
                            if document
                            else ctx.get("source_revision", 1)
                        ),
                        "context.type_id": (
                            document.get("type_id", "") if document else ""
                        ),
                    }
                },
            )
            if updated is not None:
                stats["indexed"] += 1
                if document is not None:
                    try:
                        await store.maintain_selection_indexes()
                        await store.maintain_ann(document["workspace_id"])
                    except Exception:
                        # Index maintenance is retryable; it must not turn a
                        # successfully fenced publication into failed delivery.
                        logger.warning("Search index maintenance will retry")
        except asyncio.CancelledError:
            # Lease expiry makes process loss/cancellation resumable.
            raise
        except Exception as exc:
            attempt = int(ctx.get("attempt", 0)) + 1
            await database.find_one_and_update(
                "object",
                fence,
                {
                    "$set": {
                        "context.status": "pending",
                        "context.attempt": attempt,
                        "context.due": time.time() + min(3600, 2 ** min(attempt, 10)),
                        # No source content, DSNs or provider exception strings in status.
                        "context.error": type(exc).__name__,
                    }
                },
            )
            logger.warning(
                "Index job remains pending: %s (%s)", row["id"], type(exc).__name__
            )
            stats["failed"] += 1
    return stats


def _worker_setting(name: str, default: int, maximum: int) -> int:
    try:
        return min(maximum, max(1, int(os.getenv(name, str(default)))))
    except ValueError:
        logger.error("%s must be a whole number; using %s", name, default)
        return default


async def indexing_loop() -> None:
    """Reconcile sources and deliver bounded work until shutdown."""
    from . import get_embedding_store
    from .pgvector_driver import PgVectorEmbeddingStore

    while True:
        try:
            if isinstance(get_embedding_store(), PgVectorEmbeddingStore):
                from .index_management import process_reindex_requests
                from .workspace_reindex import process_workspace_rebuilds

                await process_workspace_rebuilds()
                await process_reindex_requests()
                from app.services.attachment_metadata.analysis import (
                    reconcile_attachment_page,
                )

                await reconcile_attachment_page()
                await reconcile_page(limit=100)
                await deliver_index_jobs(
                    limit=_worker_setting("INDEX_WORKER_BATCH_SIZE", 16, 128)
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Indexing cycle will retry")
        await asyncio.sleep(_worker_setting("INDEX_WORKER_INTERVAL_SECONDS", 10, 3600))
