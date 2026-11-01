"""Incremental, non-authoritative App/Track metadata projections (I-GRAPH-02).

The primary graph owns existence, hierarchy and access. These scalar-keyed
Objects only select discovery candidates. Mutation events refresh one source;
the existing maintenance worker repairs missed events in bounded pages.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from jvspatial.core.context import get_default_context

from app.models.nodes import App, Track, Workspace
from app.models.resource_metadata import ResourceMetadataProjection
from app.services.resource_location import DATA_APP_STATES, locate_resources
from app.utils.time import utc_now_iso

DOCUMENT = "ResourceMetadataProjection"
STATE = "ResourceMetadataProgress"
PAGE = 100
MAX_RESOURCES = 5000


def identity(entity: str, key: str) -> str:
    """Use one deterministic scalar record per projection/progress key."""
    return f"o.{entity}." + hashlib.sha256(key.encode()).hexdigest()


def source_document(row: dict) -> dict | None:
    """Project bounded canonical fields, not content records or access grants."""
    entity = row.get("entity")
    context = row.get("context")
    if entity not in {App._entity_name(), Track._entity_name()} or not isinstance(
        context, dict
    ):
        return None
    kind = "app" if entity == App._entity_name() else "track"
    source_id = row.get("id")
    workspace = context.get("workspace_id")
    name = context.get("name" if kind == "app" else "title", "")
    description = context.get("description" if kind == "app" else "purpose", "")
    if (
        not isinstance(source_id, str)
        or not isinstance(workspace, str)
        or not workspace
    ):
        return None
    if not isinstance(name, str) or not isinstance(description, str):
        return None
    lifecycle = context.get("lifecycle_state", "active")
    doc = {
        "source_id": source_id,
        "workspace_id": workspace,
        "kind": kind,
        "name": name[:256],
        "description": description[:512],
        "lifecycle": lifecycle,
    }
    doc["source_digest"] = hashlib.sha256(
        json.dumps(doc, sort_keys=True).encode()
    ).hexdigest()
    return doc


async def _primary_record(collection: str, resource_id: str) -> dict | None:
    rows = await get_default_context().database.find(
        collection, {"id": resource_id}, limit=1
    )
    return rows[0] if rows else None


async def _refresh_sources(resource_ids: list[str]) -> int:
    """Read a bounded source batch after its projection versions, then CAS edits."""
    if not resource_ids or len(resource_ids) > PAGE:
        return 0
    database = get_default_context().database
    keys = {identity(DOCUMENT, source_id): source_id for source_id in resource_ids}
    previous_rows = await database.find(
        "object", {"entity": DOCUMENT, "id": {"$in": list(keys)}}, limit=len(keys)
    )
    previous = {row["id"]: row for row in previous_rows}
    # This read follows the version snapshot above: delayed writers cannot
    # overwrite a projection refreshed against a newer source in the meantime.
    source_rows = await database.find(
        "node", {"id": {"$in": resource_ids}}, limit=len(resource_ids)
    )
    sources = {row["id"]: row for row in source_rows}
    track_rows = [
        row
        for row in source_rows
        if row.get("entity") == Track._entity_name()
        and source_document(row) is not None
    ]
    track_locations = (
        await locate_resources(
            "track",
            [row["id"] for row in track_rows],
            selected_records=track_rows,
            allowed_app_states=DATA_APP_STATES,
        )
        if track_rows
        else {}
    )
    changed = 0
    for key, resource_id in keys.items():
        old = previous.get(key)
        previous_digest = (old or {}).get("context", {}).get("source_digest")
        row = sources.get(resource_id)
        doc = source_document(row) if isinstance(row, dict) else None
        if doc is not None and doc["kind"] == "track":
            location = track_locations.get(resource_id)
            doc["parent_app_id"] = location.app_id if location else None
            doc["source_digest"] = hashlib.sha256(
                json.dumps(
                    {
                        key: value
                        for key, value in doc.items()
                        if key != "source_digest"
                    },
                    sort_keys=True,
                ).encode()
            ).hexdigest()
        if doc is None:
            if old:
                deleted = await database.find_one_and_delete(
                    "object",
                    {
                        "id": key,
                        "entity": DOCUMENT,
                        "context.source_digest": previous_digest,
                    },
                )
                changed += deleted is not None
        elif previous_digest == doc["source_digest"]:
            continue
        elif old is None:
            result = await database.insert_if_absent(
                "object", {"id": key, "entity": DOCUMENT, "context": doc}
            )
            changed += result.created
        else:
            updated = await database.find_one_and_update(
                "object",
                {
                    "id": key,
                    "entity": DOCUMENT,
                    "context.source_digest": previous_digest,
                },
                {"$set": {"context": doc}},
            )
            changed += updated is not None
    return changed


async def refresh_resource_metadata(resource_id: str) -> bool:
    """Refresh a current source using compare-and-swap and uncached primary reads."""
    return bool(await _refresh_sources([resource_id]))


async def request_metadata_index(workspace_id: str) -> dict:
    """Retain bootstrap intent without overwriting another worker's progress."""
    context = get_default_context()
    await context.ensure_indexes(ResourceMetadataProjection)
    database = context.database
    key = identity(STATE, workspace_id)
    existing = await _primary_record("object", key)
    if existing:
        return existing["context"]
    row = {
        "id": key,
        "entity": STATE,
        "context": {
            "workspace_id": workspace_id,
            "phase": "apps",
            "after": "",
            "ready": False,
            "revision": 0,
            "completed_at": None,
        },
    }
    await database.insert_if_absent("object", row)
    return row["context"]


async def reconcile_metadata_page(workspace_id: str) -> dict:
    """Repair one bounded source or tombstone page; durable progress survives restart."""
    database = get_default_context().database
    state = dict(await request_metadata_index(workspace_id))
    before_revision = state.get("revision")
    after = state.get("after") or ""
    phase = state.get("phase", "apps")
    if phase == "sources":  # Resume an earlier scalar-only projection safely.
        phase, after = "apps", ""
    if phase in {"apps", "tracks"}:
        from app.services.walkers.resource_metadata_page import (
            ResourceMetadataPageWalker,
        )

        rows = await database.find(
            "node", {"entity": Workspace._entity_name(), "id": workspace_id}, limit=1
        )
        if rows:
            workspace = Workspace.model_validate(
                {**rows[0]["context"], "id": workspace_id}
            )
            walker = ResourceMetadataPageWalker(
                workspace_id=workspace_id, kind=phase, cursor=after or None
            )
            await walker.spawn(workspace)
            if any(
                isinstance(report, dict) and "hook_error" in report
                for report in await walker.get_report()
            ):
                raise RuntimeError("Metadata source traversal failed")
            await _refresh_sources(walker.source_ids)
            state.update(phase=phase, after=walker.next_cursor or "")
            if walker.next_cursor is None:
                state.update(
                    phase="tracks" if phase == "apps" else "tombstones", after=""
                )
        else:
            state.update(phase="tombstones", after="")
    else:
        query: dict[str, Any] = {
            "entity": DOCUMENT,
            "context.workspace_id": workspace_id,
        }
        if after:
            query["id"] = {"$gt": after}
        rows = await database.find("object", query, limit=PAGE, sort=[("id", 1)])
        await _refresh_sources([row["context"]["source_id"] for row in rows])
        state["after"] = rows[-1]["id"] if rows else ""
        if len(rows) < PAGE:
            state.update(phase="apps", after="", ready=True, completed_at=utc_now_iso())
    state["revision"] = (before_revision or 0) + 1
    result = await database.find_one_and_update(
        "object",
        {
            "id": identity(STATE, workspace_id),
            "entity": STATE,
            "context.revision": (
                before_revision if before_revision is not None else {"$exists": False}
            ),
        },
        {"$set": {"context": state}},
    )
    if result is None:
        return await request_metadata_index(workspace_id)
    return state


async def reconcile_requested_metadata() -> None:
    """Fair keyset scheduling reuses the existing indexing worker lifecycle."""
    database = get_default_context().database
    # Backfill known workspaces without waiting for an agent's first search.
    bootstrap_id = identity("ResourceMetadataScheduler", "workspace-bootstrap")
    bootstrap = await _primary_record("object", bootstrap_id)
    last_workspace = (bootstrap or {}).get("context", {}).get("after", "")
    workspace_query: dict[str, Any] = {"entity": Workspace._entity_name()}
    if last_workspace:
        workspace_query["id"] = {"$gt": last_workspace}
    workspaces = await database.find("node", workspace_query, limit=5, sort=[("id", 1)])
    for workspace in workspaces:
        await request_metadata_index(workspace["id"])
    await database.save(
        "object",
        {
            "id": bootstrap_id,
            "entity": "ResourceMetadataScheduler",
            "context": {"after": workspaces[-1]["id"] if len(workspaces) == 5 else ""},
        },
    )
    cursor_id = identity("ResourceMetadataScheduler", "cursor")
    cursor = await _primary_record("object", cursor_id)
    after = (cursor or {}).get("context", {}).get("after", "")
    query: dict[str, Any] = {"entity": STATE}
    if after:
        query["id"] = {"$gt": after}
    states = await database.find("object", query, limit=5, sort=[("id", 1)])
    for row in states:
        try:
            await reconcile_metadata_page(row["context"]["workspace_id"])
        except Exception:
            import logging

            logging.getLogger(__name__).exception(
                "Metadata repair failed for workspace %s",
                row["context"]["workspace_id"],
            )
            # Failed pages remain pending; a broken tenant cannot stop peers.

    await database.save(
        "object",
        {
            "id": cursor_id,
            "entity": "ResourceMetadataScheduler",
            "context": {"after": states[-1]["id"] if len(states) == 5 else ""},
        },
    )


async def indexed_inventory(
    *,
    user_id: str,
    workspace_id: str,
    query: str,
    offset: int,
    limit: int,
    ranking_mode: str,
) -> dict | None:
    """Select a page with live authority; denied candidates do not consume slots."""
    state = await request_metadata_index(workspace_id)
    if not state.get("ready"):
        return None  # Explicit canonical fallback while bootstrap is pending.
    database = get_default_context().database
    rows = []
    after = ""
    while True:
        selection: dict[str, Any] = {
            "entity": DOCUMENT,
            "context.workspace_id": workspace_id,
        }
        if after:
            selection["id"] = {"$gt": after}
        page = await database.find("object", selection, limit=250, sort=[("id", 1)])
        rows.extend(page)
        if len(rows) > MAX_RESOURCES:
            return None  # Existing canonical budget/error handles oversized workspaces.
        if len(page) < 250:
            break
        after = page[-1]["id"]
    from app.agentive.harness.capability_search import _rank
    from app.schemas.policy import Resource, Subject
    from app.services.permissions import resolve_role
    from app.services.policy_engine import evaluate

    docs = {row["context"]["source_id"]: row["context"] for row in rows}
    texts = [(key, doc["name"] + " " + doc["description"]) for key, doc in docs.items()]
    lexical = _rank(query, texts)
    scores = {key: 1 / (60 + rank) for rank, (key, _) in enumerate(lexical, 1)}
    mode = "lexical"
    if ranking_mode != "lexical" and texts:
        import asyncio
        import math

        from app.agentive.harness.capability_search import (
            _definition_vectors,
            _embed_text_for_capability_search,
        )

        async def semantic():
            q = await _embed_text_for_capability_search(query)
            vectors = await _definition_vectors([text for _, text in texts])
            if not q or not all(math.isfinite(value) for value in q):
                raise ValueError("Invalid query vector")
            qnorm = math.sqrt(sum(v * v for v in q)) or 1
            ranks = []
            for (key, _), vector in zip(texts, vectors):
                if len(q) != len(vector):
                    raise ValueError("Incompatible metadata embedding")
                norm = math.sqrt(sum(v * v for v in vector)) or 1
                ranks.append(
                    (key, sum(a * b for a, b in zip(q, vector)) / qnorm / norm)
                )
            return sorted(ranks, key=lambda pair: (-pair[1], pair[0]))

        try:
            semantic_ranks = await asyncio.wait_for(semantic(), timeout=2)
            for rank, (key, _) in enumerate(semantic_ranks, 1):
                scores[key] = scores.get(key, 0) + 1 / (60 + rank)
            mode = "hybrid"
        except Exception:
            if ranking_mode == "hybrid":
                from app.api.errors import BadRequestError

                raise BadRequestError(
                    message="Semantic resource ranking is unavailable; restart with lexical mode"
                ) from None
    ordered = sorted(docs, key=lambda key: (-scores.get(key, 0), key))
    admitted: dict[str, Any] = {}
    app_cache: dict[str, Any] = {}
    track_cache: dict[str, Any] = {}
    parents: dict[str, str] = {}
    subject = Subject(kind="human", id=user_id)

    async def app_for(app_id):
        if app_id not in app_cache:
            source = await _primary_record("node", app_id)
            app = (
                App.model_validate({**source.get("context", {}), "id": app_id})
                if source and source.get("entity") == App._entity_name()
                else None
            )
            location = (
                await locate_resources("app", [app_id], workspace_hints=[workspace_id])
            ).get(app_id)
            app_cache[app_id] = (
                app
                if (
                    app
                    and location
                    and location.workspace_id == workspace_id
                    and app.lifecycle_state == "active"
                    and await resolve_role(user_id, "app", app_id) is not None
                )
                else None
            )
        return app_cache[app_id]

    for resource_id in ordered:
        doc = docs[resource_id]
        # Hierarchy hints suppress repeated admission of children after their
        # App has been proved live. Hints never admit or disclose a child: the
        # small preview below still uses current containment and Track policy.
        parent_hint = doc.get("parent_app_id")
        if (
            parent_hint in admitted
            and sum(value == parent_hint for value in parents.values()) >= 4
        ):
            continue
        if doc["kind"] == "app":
            app = await app_for(resource_id)
            if app is not None:
                admitted.setdefault(app.id, app)
        else:
            source = await _primary_record("node", resource_id)
            track = (
                Track.model_validate({**source.get("context", {}), "id": resource_id})
                if source and source.get("entity") == Track._entity_name()
                else None
            )
            location = (
                await locate_resources(
                    "track",
                    [resource_id],
                    workspace_hints=[workspace_id],
                    allowed_app_states=DATA_APP_STATES,
                )
            ).get(resource_id)
            if not track or not location or location.workspace_id != workspace_id:
                continue
            decision = await evaluate(
                subject=subject,
                action="track.read",
                resource=Resource(
                    kind="track", id=resource_id, scope=f"workspace:{workspace_id}"
                ),
            )
            if not decision.allowed:
                continue
            track_cache[resource_id] = track
            parents[resource_id] = location.app_id
            parent = await app_for(location.app_id) if location.app_id else None
            if parent:
                admitted.setdefault(parent.id, parent)
            else:
                admitted.setdefault(resource_id, track)
        if len(admitted) >= offset + limit + 1:
            break
    values = list(admitted.values())
    selected = values[offset : offset + limit]
    return {
        "resources": selected,
        "tracks": list(track_cache.values()),
        "parents": parents,
        "mode": mode,
        "has_more": len(values) > offset + limit,
        "coverage": {
            "kind": "eventually_consistent_metadata",
            "complete": False,
            "reconciled_at": state.get("completed_at"),
            "record_evidence": False,
        },
        "offset": offset,
    }
