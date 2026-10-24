"""Leased, rooted rebuilds and verified serving-profile activation.

Enumeration is workspace-directed and resumable beyond 1,000 records. A second
pass verifies stored manifests against current sources. A committed-intent
journal catches up subsequent changes before a short fenced activation;
queue exhaustion and allocated timestamps alone never establish readiness.
"""

import asyncio
import time
import uuid
from dataclasses import asdict

from jvspatial.core.context import get_default_context

from app.schemas.embedding_configuration import EmbeddingModelChoice
from app.services.entry_write_scope import entry_write_scope
from app.services.workspace_model_configuration import configuration_record

from .embedding_configuration import REBUILD, owner_subject, selected_profile
from .indexing import ENTITY, _enqueue_document, index_document
from .migration_barrier import barrier


async def _schedule(database, fence, *, phase, due=0, **values):
    await database.find_one_and_update(
        "object",
        fence,
        {
            "$set": {
                "context.status": "pending",
                "context.phase": phase,
                "context.due": time.time() + due,
                **{"context." + key: value for key, value in values.items()},
            }
        },
    )


async def _journal(database, ctx, *, limit=100, include_pending=False):
    selector = {
        "context.intent_epoch": {
            "$gt": ctx.get("catchup_after", ctx.get("verify_epoch", 0))
        }
    }
    if include_pending:
        selector = {
            "$or": [selector, {"context.status": {"$in": ["pending", "working"]}}]
        }
    return await database.find(
        "object",
        {
            "entity": ENTITY,
            "context.workspace_id": ctx["workspace_id"],
            "context.profile_id": ctx["profile_id"],
            **selector,
        },
        sort=[("context.intent_epoch", 1), ("id", 1)],
        limit=limit,
    )


async def _activate(row, fence, profile):
    """Close a committed journal tail under a short writer/request fence."""
    from . import get_embedding_store
    from .migration_barrier import scope_epoch
    from .passage_store import passage_store

    ctx = row["context"]
    async with entry_write_scope(ctx["workspace_id"]) as graph:
        epoch = await barrier(ctx["workspace_id"], lock=True)
        current = await graph.database.find_one_and_update(
            "object",
            {
                **fence,
                "context.status": "working",
                "context.lease_until": {"$gt": time.time()},
            },
            {"$set": {"context.activation_checked_at": time.time()}},
        )
        data = (current or {}).get("context", {})
        if not current:
            return
        config = await configuration_record(ctx["workspace_id"])
        if (
            config is None
            or config.embedding_settings_revision != ctx["revision"]
            or await owner_subject(ctx["workspace_id"]) != ctx["requested_by"]
            or (
                await selected_profile(
                    ctx["workspace_id"],
                    EmbeddingModelChoice.model_validate(config.embedding_settings),
                )
            ).id
            != profile.id
        ):
            await graph.database.find_one_and_update(
                "object", fence, {"$set": {"context.status": "superseded"}}
            )
            return
        scope = await scope_epoch(ctx["workspace_id"])
        if scope != ctx.get("verify_scope_epoch", 0):
            await _schedule(
                graph.database,
                fence,
                phase="verifying",
                stack=[],
                verified=0,
                verify_epoch=epoch,
                verify_scope_epoch=scope,
                catchup_after=epoch,
            )
            return
        # A bounded final tail avoids requiring even a short idle interval.
        # Larger tails are checked outside the writer fence in paged catch-up.
        changed = await _journal(graph.database, ctx, limit=17, include_pending=True)
        if len(changed) > 16 or any(
            r["context"].get("status") != "indexed" for r in changed
        ):
            await _schedule(graph.database, fence, phase="catching_up", due=1)
            return
        active = [r for r in changed if r["context"].get("source_hash") != "excluded"]
        verified = await passage_store(get_embedding_store(), profile).verified_sources(
            [r["context"]["entry_id"] for r in active]
        )
        if any(
            verified.get(r["context"]["entry_id"]) != r["context"].get("source_hash")
            for r in active
        ):
            await _schedule(graph.database, fence, phase="catching_up", due=1)
            return
        if data.get("lease_until", 0) <= time.time():
            return
        updated = await graph.database.find_one_and_update(
            "node",
            {"id": config.id, "context.embedding_settings_revision": ctx["revision"]},
            {
                "$set": {
                    "context.embedding_serving_settings": config.embedding_settings,
                    "context.embedding_serving_profile": asdict(profile),
                    "context.embedding_continue_previous_provider": False,
                }
            },
        )
        if updated is not None:
            await graph.database.find_one_and_update(
                "object",
                fence,
                {
                    "$set": {
                        "context.status": "ready",
                        "context.phase": "ready",
                        "context.ready_at": time.time(),
                        "context.verified_epoch": epoch,
                        "context.verified": ctx.get("verified", 0),
                        "context.error": "",
                    }
                },
            )


async def _catch_up(row, fence, profile):
    from app.models.nodes import Entry

    from . import get_embedding_store
    from .passage_store import passage_store

    ctx = row["context"]
    database = get_default_context().database
    rows = await _journal(database, ctx, include_pending=True)
    if not rows:
        await _activate(row, fence, profile)
        return
    stored = await passage_store(get_embedding_store(), profile).verified_sources(
        [
            r["context"]["entry_id"]
            for r in rows
            if r["context"].get("source_hash") != "excluded"
        ]
    )
    after = ctx.get("catchup_after", ctx.get("verify_epoch", 0))
    for item in rows:
        receipt = item["context"]
        entry = await Entry.find_one({"id": receipt["entry_id"]})
        document = await index_document(entry) if entry else None
        if document is None or document["workspace_id"] != ctx["workspace_id"]:
            if receipt.get("status") != "indexed":
                await database.find_one_and_update(
                    "object",
                    {"id": item["id"], "context.generation": receipt["generation"]},
                    {"$set": {"context.status": "superseded"}},
                )
            after = max(after, receipt.get("intent_epoch", 0))
            continue
        if receipt.get("status") in {"pending", "working"}:
            await _schedule(
                database, fence, phase="catching_up", due=10, catchup_after=after
            )
            return
        if (
            receipt.get("status") != "indexed"
            or receipt.get("source_hash") != document["source_hash"]
            or receipt.get("type_id", "") != document.get("type_id", "")
            or stored.get(entry.id) != document["source_hash"]
        ):
            await _enqueue_document(entry, document, verify_integrity=True)
        after = max(after, receipt.get("intent_epoch", 0))
    ctx["catchup_after"] = after
    await _schedule(database, fence, phase="catching_up", catchup_after=after, error="")


async def _page(row, fence, profile):
    from app.services.resource_location import DATA_APP_STATES, locate_resources
    from app.services.walkers.workspace_index_page import WorkspaceIndexPageWalker

    from . import get_embedding_store
    from .migration_barrier import scope_epoch
    from .passage_store import passage_store

    ctx = row["context"]
    if ctx.get("protocol_version") != 2:
        # Older receipts did not retain a committed-intent watermark. Their
        # partial verification cannot establish coverage under this protocol.
        await _schedule(
            get_default_context().database,
            fence,
            phase="enumerating",
            protocol_version=2,
            stack=[],
            enumerated=0,
            verified=0,
            catchup_after=0,
            error="",
        )
        return
    phase = ctx.get("phase", "enumerating")
    if phase == "catching_up":
        await _catch_up(row, fence, profile)
        return
    if phase == "verifying" and "verify_epoch" not in ctx:
        ctx["verify_epoch"] = await barrier(ctx["workspace_id"])
        ctx["verify_scope_epoch"] = await scope_epoch(ctx["workspace_id"])
    entries, stack = await WorkspaceIndexPageWalker(
        workspace_id=ctx["workspace_id"], frames=ctx.get("stack", []), page_size=100
    ).resume()
    locations = await locate_resources(
        "entry",
        [entry.id for entry in entries],
        workspace_hints=[ctx["workspace_id"]],
        allowed_app_states=DATA_APP_STATES,
    )
    schemas = {}
    documents = []
    for entry in entries:
        location = locations.get(entry.id)
        document = (
            await index_document(entry, location=location, schemas=schemas)
            if location
            else None
        )
        if document is not None and document["workspace_id"] != ctx["workspace_id"]:
            continue
        documents.append((entry, document))
    verified = {}
    if phase == "verifying":
        verified = await passage_store(get_embedding_store(), profile).verified_sources(
            [entry.id for entry, document in documents if document is not None]
        )
    for entry, document in documents:
        if phase != "verifying" or (
            document is not None and verified.get(entry.id) != document["source_hash"]
        ):
            await _enqueue_document(entry, document, verify_integrity=True)
    updates = {
        "stack": stack,
        "error": "",
        "enumerated": int(ctx.get("enumerated", 0))
        + (len(documents) if phase == "enumerating" else 0),
        "verified": int(ctx.get("verified", 0))
        + (len(documents) if phase == "verifying" else 0),
    }
    if phase == "verifying":
        updates.update(
            verify_epoch=ctx["verify_epoch"],
            verify_scope_epoch=ctx.get("verify_scope_epoch", 0),
        )
    if not stack and phase == "enumerating":
        phase = "verifying"
        epoch = await barrier(ctx["workspace_id"])
        updates.update(
            verified=0,
            verify_epoch=epoch,
            verify_scope_epoch=await scope_epoch(ctx["workspace_id"]),
            catchup_after=epoch,
        )
    if not stack and phase == "verifying" and ctx.get("phase") == "verifying":
        updates["phase"] = "catching_up"
        await get_default_context().database.find_one_and_update(
            "object",
            fence,
            {"$set": {"context." + key: value for key, value in updates.items()}},
        )
        ctx.update(updates)
        await _activate(row, fence, profile)
        return
    await _schedule(get_default_context().database, fence, phase=phase, **updates)


async def process_workspace_rebuilds(*, limit: int = 2):
    """Lease bounded slices, with current owner and configuration fencing."""
    database = get_default_context().database
    rows = await database.find(
        "object",
        {
            "entity": REBUILD,
            "$or": [
                {
                    "context.status": "pending",
                    "$or": [
                        {"context.due": {"$lte": time.time()}},
                        {"context.due": {"$exists": False}},
                    ],
                },
                {
                    "context.status": "working",
                    "context.lease_until": {"$lte": time.time()},
                },
                # Adopt old enumeration-only receipts by verifying their sources.
                {"context.status": "queued"},
            ],
        },
        sort=[("context.due", 1), ("id", 1)],
        limit=limit,
    )
    for row in rows:
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
            config = await configuration_record(ctx["workspace_id"])
            if (
                config is None
                or config.embedding_settings_revision != ctx["revision"]
                or await owner_subject(ctx["workspace_id"]) != ctx["requested_by"]
            ):
                await database.find_one_and_update(
                    "object", fence, {"$set": {"context.status": "superseded"}}
                )
                continue
            profile = await selected_profile(
                ctx["workspace_id"],
                EmbeddingModelChoice.model_validate(config.embedding_settings),
            )
            if profile.id != ctx["profile_id"]:
                await database.find_one_and_update(
                    "object", fence, {"$set": {"context.status": "superseded"}}
                )
                continue
            await _page(row, fence, profile)
        except asyncio.CancelledError:
            # Graceful deploys need not wait out a crashed-worker lease. The
            # lease/generation CAS releases only this worker's request.
            try:
                await _schedule(
                    database,
                    {**fence, "context.status": "working"},
                    phase=ctx.get("phase", "enumerating"),
                )
            finally:
                raise
        except Exception as exc:
            attempt = int(ctx.get("attempt", 0)) + 1
            await database.find_one_and_update(
                "object",
                fence,
                {
                    "$set": {
                        "context.status": "pending",
                        "context.error": type(exc).__name__,
                        "context.attempt": attempt,
                        "context.due": time.time() + min(3600, 2 ** min(attempt, 10)),
                    }
                },
            )
