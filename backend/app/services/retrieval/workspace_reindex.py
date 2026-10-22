"""Bounded, leased workspace rebuild cursors; queued never means indexed."""

import time
import uuid

from jvspatial.core.context import get_default_context

from .embedding_configuration import (
    REBUILD,
    owner_subject,
    resolve_workspace_embedding_profile,
)
from .indexing import enqueue_entry, index_document


async def process_workspace_rebuilds(*, limit: int = 2):
    """Lease a bounded page and queue only canonically contained workspace entries."""
    from app.models.nodes import Entry

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
            profile = await resolve_workspace_embedding_profile(ctx["workspace_id"])
            if (
                await owner_subject(ctx["workspace_id"]) != ctx["requested_by"]
                or profile.id != ctx["profile_id"]
            ):
                await database.find_one_and_update(
                    "object", fence, {"$set": {"context.status": "superseded"}}
                )
                continue
            # Scan the canonical Entry collection rather than trusting a possibly
            # stale workspace cache; index_document verifies rooted membership.
            entries = await database.find(
                "node",
                {"entity": "Entry", "id": {"$gt": ctx["after"]}},
                sort=[("id", 1)],
                limit=100,
            )
            queued = 0
            for record in entries:
                entry = await Entry.find_one({"id": record["id"]})
                document = await index_document(entry) if entry else None
                if document and document["workspace_id"] == ctx["workspace_id"]:
                    await enqueue_entry(entry, force=True)
                    queued += 1
            await database.find_one_and_update(
                "object",
                fence,
                {
                    "$set": {
                        "context.after": entries[-1]["id"] if entries else ctx["after"],
                        "context.status": "queued" if len(entries) < 100 else "pending",
                        "context.queued": ctx["queued"] + queued,
                        "context.error": "",
                    }
                },
            )
        except Exception as exc:
            await database.find_one_and_update(
                "object",
                fence,
                {
                    "$set": {
                        "context.status": "pending",
                        "context.error": type(exc).__name__,
                        "context.attempt": int(ctx.get("attempt", 0)) + 1,
                        "context.due": time.time()
                        + min(3600, 2 ** min(int(ctx.get("attempt", 0)) + 1, 10)),
                    }
                },
            )
