"""Scoped index-management capabilities using ordinary Track authority."""

from __future__ import annotations

import time
import uuid

from jvspatial.core.context import get_default_context

from app.api.errors import InsufficientPermissionsError, ResourceNotFoundError
from app.services.agent_scope import active_workspace_id

from .embedding_configuration import resolve_workspace_embedding_profile
from .index_definition import IndexDefinition, digest


async def admit_track(user_id: str, track_id: str, workspace_id: str | None = None):
    """Require current configuration authority in the bound workspace."""
    from app.models.nodes import Track
    from app.services.permissions import can_admin_track
    from app.services.resource_location import MANAGEMENT_APP_STATES, locate_resource

    bound = active_workspace_id()
    if bound and workspace_id and workspace_id != bound:
        raise InsufficientPermissionsError(
            message="This list is outside your workspace"
        )
    workspace_id = workspace_id or bound
    location = await locate_resource(
        "track", track_id, allowed_app_states=MANAGEMENT_APP_STATES
    )
    if not workspace_id or location is None or location.workspace_id != workspace_id:
        raise InsufficientPermissionsError(
            message="This list is outside your workspace"
        )
    if not await can_admin_track(user_id, track_id):
        raise InsufficientPermissionsError(
            message="You cannot manage search for this list"
        )
    track = await Track.find_one({"id": track_id})
    if track is None:
        raise ResourceNotFoundError(message="List not found")
    return track


async def request_reindex(
    user_id: str, track_id: str, workspace_id: str | None = None
) -> dict:
    """Retain a rebuild request attributed to its authorized actor."""
    await admit_track(user_id, track_id, workspace_id)
    profile = await resolve_workspace_embedding_profile(
        workspace_id or active_workspace_id()
    )
    identity = "o.TrackIndexRequest." + digest([track_id, profile.id])
    await get_default_context().database.save(
        "object",
        {
            "id": identity,
            "entity": "TrackIndexRequest",
            "context": {
                "track_id": track_id,
                "workspace_id": workspace_id or active_workspace_id(),
                "requested_by": user_id,
                "profile_id": profile.id,
                "status": "pending",
                "after": "",
                "requested_at": time.time(),
                "generation": uuid.uuid4().hex,
                "queued": 0,
            },
        },
    )
    return {"requested": True, "track_id": track_id, "profile_id": profile.id}


async def configure_index(
    user_id: str,
    track_id: str,
    definition: dict,
    workspace_id: str | None = None,
    expected_revision: int | None = None,
) -> dict:
    """Commit selection, rebuild intent and a durable audit fact together."""
    from app.api.errors import ResourceConflictError
    from app.services.approved_effect import (
        approved_capture_active,
        approved_event_sink,
    )
    from app.services.entry_write_scope import (
        entry_write_scope,
        is_postgres_entry_transaction,
    )

    definition = IndexDefinition.model_validate(definition).model_dump()
    caller = get_default_context()
    outbox_id = None
    captured = await approved_capture_active()
    async with entry_write_scope(track_id) as graph:
        track = await admit_track(user_id, track_id, workspace_id)
        if (
            expected_revision is not None
            and track.search_index_revision != expected_revision
        ):
            raise ResourceConflictError(
                message="Search settings changed. Review the latest settings before trying again."
            )
        before = track.search_index
        track.search_index = definition
        track.search_index_revision += 1
        await track.save()
        result = await request_reindex(user_id, track_id, workspace_id)
        event = {
            "actor_kind": "human",
            "actor_id": user_id,
            "action": "track.update",
            "resource_type": "Track",
            "resource_id": track_id,
            "before": {"search_index": before},
            "after": {
                "search_index": definition,
                "search_index_revision": track.search_index_revision,
            },
            "scope": f"track:{track_id}",
        }
        if captured:
            approved_event_sink(event)
        elif is_postgres_entry_transaction(graph.database):
            from app.services.app_operations.event_outbox import insert_revision_event

            outbox_id = await insert_revision_event(
                transaction=graph.database,
                workspace_id=track.workspace_id,
                namespace="search-index-config-v1",
                subject_id=track.id,
                revision=track.search_index_revision,
                event=event,
            )
    # Never open the audit logger's independent connection under a primary PG
    # transaction: first-use schema provisioning can deadlock that transaction.
    if outbox_id and graph is not caller:
        import logging

        from app.services.app_operations.event_outbox import deliver_operation_event

        try:
            await deliver_operation_event(outbox_id=outbox_id)
        except Exception:
            logging.getLogger(__name__).exception(
                "Search configuration audit awaits recovery"
            )
    elif not captured and not outbox_id:
        from app.services.change_event import emit_change_event

        await emit_change_event(**event)
    return {**result, "definition": definition, "revision": track.search_index_revision}


async def index_status(
    user_id: str, track_id: str, workspace_id: str | None = None
) -> dict:
    """Compare storage integrity and bounded source coverage without inference."""
    from . import get_embedding_store
    from .indexing import ENTITY
    from .passage_store import passage_store
    from .pgvector_driver import PgVectorEmbeddingStore

    track = await admit_track(user_id, track_id, workspace_id)
    driver = get_embedding_store()
    try:
        profile = await resolve_workspace_embedding_profile(
            workspace_id or active_workspace_id()
        )
    except (ValueError, RuntimeError):
        return {
            "track_id": track_id,
            "profile_id": None,
            "store_ready": False,
            "error": "InvalidEmbeddingProfile",
            "jobs": {},
            "coverage_complete": False,
            "message": "Search needs a configuration update. Your records are still available.",
        }
    ready, error = False, ""
    if isinstance(driver, PgVectorEmbeddingStore):
        try:
            await passage_store(driver, profile).ensure_schema()
            # Reading status must never make a paid inference request. Storage
            # integrity and recent delivery state are distinct from model health.
            ready = True
        except Exception as exc:
            error = type(exc).__name__
    database = get_default_context().database
    rows = await database.find(
        "object",
        {
            "entity": ENTITY,
            "context.track_id": track_id,
            "context.profile_id": profile.id,
        },
        limit=1001,
    )
    counts = {"pending": 0, "working": 0, "indexed": 0, "failed": 0}
    for row in rows[:1000]:
        data = row["context"]
        if data["status"] in counts:
            counts[data["status"]] += 1
        if data.get("error"):
            counts["failed"] += 1
    # Compare bounded CURRENT sources with persisted delivery receipts AND the
    # actual passage table. Queue counts alone cannot establish index coverage.
    from app.models.nodes import Entry

    from .indexing import index_document

    sources = await database.find(
        "node",
        {
            "entity": "Entry",
            "context.track_id": track_id,
        },
        sort=[("id", 1)],
        limit=1001,
    )
    receipts = {r["context"]["entry_id"]: r["context"] for r in rows}
    stored = {}
    if ready:
        store = passage_store(driver, profile)
        stored = await store.verified_sources([r["id"] for r in sources[:1000]])
    current, excluded, invalid = 0, 0, 0
    for source in sources[:1000]:
        try:
            entry = await Entry.find_one({"id": source["id"]})
            document = await index_document(entry) if entry else None
        except Exception:
            invalid += 1
            continue
        if document is None:
            excluded += 1
            continue
        receipt = receipts.get(source["id"], {})
        source_hash = document["source_hash"]
        if (
            receipt.get("status") == "indexed"
            and receipt.get("indexed_hash") == source_hash
            and (not document["texts"] or stored.get(source["id"]) == source_hash)
        ):
            current += 1
    truncated = len(sources) > 1000 or len(rows) > 1000
    return {
        "track_id": track_id,
        "definition": IndexDefinition.model_validate(track.search_index).model_dump(),
        "profile_id": profile.id,
        "model": profile.model,
        "dimensions": profile.dimensions,
        "store_ready": ready,
        "error": error,
        "jobs": counts,
        "coverage_complete": ready
        and not truncated
        and current + excluded == len(sources),
        "coverage": {
            "examined": min(len(sources), 1000),
            "current": current,
            "excluded": excluded,
            "invalid": invalid,
        },
        "counts_truncated": truncated,
        "message": "Coverage is reconciled in bounded pages; readiness is not a freshness guarantee.",
    }


async def process_reindex_requests(*, limit: int = 4) -> None:
    """Lease bounded rebuild pages and recheck request actor authority."""
    from app.schemas.policy import Resource, Subject
    from app.services.policy_engine import evaluate

    from .indexing import enqueue_record

    database = get_default_context().database
    rows = await database.find(
        "object",
        {
            "entity": "TrackIndexRequest",
            "$or": [
                {"context.status": "pending"},
                {
                    "context.status": "working",
                    "context.lease_until": {"$lte": time.time()},
                },
            ],
        },
        limit=limit,
    )
    for row in rows:
        ctx = row["context"]
        fence = {
            "id": row["id"],
            "context.generation": ctx["generation"],
            "context.after": ctx["after"],
        }
        lease = uuid.uuid4().hex
        claimed = await database.find_one_and_update(
            "object",
            {
                **fence,
                "context.status": ctx["status"],
                **(
                    {"context.lease_until": ctx["lease_until"]}
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
        fence["context.lease"] = lease
        try:
            await admit_track(ctx["requested_by"], ctx["track_id"], ctx["workspace_id"])
            if (
                await resolve_workspace_embedding_profile(ctx["workspace_id"])
            ).id != ctx["profile_id"]:
                await database.find_one_and_update(
                    "object", fence, {"$set": {"context.status": "superseded"}}
                )
                continue
        except (InsufficientPermissionsError, ResourceNotFoundError):
            await database.find_one_and_update(
                "object", fence, {"$set": {"context.status": "revoked"}}
            )
            continue
        entries = await database.find(
            "node",
            {
                "entity": "Entry",
                "context.track_id": ctx["track_id"],
                "id": {"$gt": ctx["after"]},
            },
            sort=[("id", 1)],
            limit=100,
        )
        queued = 0
        for record in entries:
            decision = await evaluate(
                subject=Subject(kind="human", id=ctx["requested_by"]),
                action="entry.read",
                resource=Resource(
                    kind="entry", id=record["id"], scope=f"track:{ctx['track_id']}"
                ),
            )
            if decision.allowed:
                await enqueue_record(record, force=True)
                queued += 1
        await database.find_one_and_update(
            "object",
            fence,
            {
                "$set": {
                    "context.after": entries[-1]["id"] if entries else ctx["after"],
                    "context.status": "queued" if len(entries) < 100 else "pending",
                    "context.queued": ctx["queued"] + queued,
                }
            },
        )
