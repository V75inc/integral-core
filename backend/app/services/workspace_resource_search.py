"""Bounded discovery of currently accessible workspace resource metadata.

This first implementation ranks live canonical metadata; it is not a durable
index and is never an authorization grant or a source of record data.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from typing import Any

from app.api.errors import BadRequestError
from app.schemas.policy import Resource, Subject
from app.services.agent_scope import (
    accessible_tracks_for_scope,
    callable_apps_for_scope,
)
from app.services.policy_engine import evaluate
from app.services.resource_location import DATA_APP_STATES, locate_resources
from app.services.workspace_tools import list_workspace_tools

logger = logging.getLogger(__name__)


async def discover_workspace_resources(
    *,
    user_id: str,
    workspace_id: str | None = None,
    query: str,
    limit: int = 6,
    offset: int = 0,
    ranking_mode: str = "lexical",
) -> dict[str, Any]:
    """Rank Apps and Tracks, then disclose only currently authorized metadata."""
    if not workspace_id:
        raise BadRequestError(message="An active workspace is required")
    if not isinstance(query, str) or not 2 <= len(query.strip()) <= 500:
        raise BadRequestError(message="Provide a search between 2 and 500 characters")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 10:
        raise BadRequestError(message="Select between 1 and 10 results")
    if (
        not isinstance(offset, int)
        or isinstance(offset, bool)
        or not 0 <= offset <= 5000
    ):
        raise BadRequestError(message="Invalid resource search offset")
    if ranking_mode not in {"auto", "lexical", "hybrid"} or (
        offset and ranking_mode == "auto"
    ):
        raise BadRequestError(
            message="For later pages use the ranking mode returned by the first page"
        )

    apps = await callable_apps_for_scope(user_id, workspace_id=workspace_id)
    tracks = await accessible_tracks_for_scope(user_id, workspace_id=workspace_id)
    if len(apps) + len(tracks) > 5000:
        raise BadRequestError(message="Narrow this workspace resource search")
    app_locations = await locate_resources(
        "app", [a.id for a in apps], workspace_hints=[workspace_id]
    )
    apps = [
        a
        for a in apps
        if getattr(a, "lifecycle_state", "") == "active"
        and app_locations.get(a.id) is not None
        and app_locations[a.id].workspace_id == workspace_id
    ]
    app_by_id = {a.id: a for a in apps}
    locations = await locate_resources(
        "track",
        [t.id for t in tracks],
        workspace_hints=[workspace_id],
        allowed_app_states=DATA_APP_STATES,
    )
    subject = Subject(kind="human", id=user_id)
    documents: list[tuple[str, str, dict[str, Any]]] = []
    child_terms: dict[str, list[str]] = {}
    child_tracks: dict[str, list[dict[str, Any]]] = {}
    for app in apps:
        name = str(app.name or "")[:256]
        description = str(app.description or "")[:512]
        documents.append(
            (
                app.id,
                name + " " + description,
                {
                    "kind": "app",
                    "id": app.id,
                    "name": name,
                    "description": description,
                    "url": f"/apps/{app.id}",
                },
            )
        )
    scoped_tracks = [
        track
        for track in tracks
        if locations.get(track.id) is not None
        and locations[track.id].workspace_id == workspace_id
    ]
    # Every Track is still checked against the live policy engine. Bound
    # concurrency avoids serial graph round trips without flooding the pool.
    policy_limit = asyncio.Semaphore(8)

    async def check_track(track):
        async with policy_limit:
            return await evaluate(
                subject=subject,
                action="track.read",
                resource=Resource(
                    kind="track", id=track.id, scope=f"workspace:{workspace_id}"
                ),
            )

    decisions = await asyncio.gather(*(check_track(track) for track in scoped_tracks))
    for track, decision in zip(scoped_tracks, decisions):
        if not decision.allowed:
            continue
        location = locations[track.id]
        name = str(track.title or "")[:256]
        purpose = str(track.purpose or "")[:512]
        item = {
            "kind": "track",
            "id": track.id,
            "name": name,
            "description": purpose,
            "url": f"/tracks/{track.id}",
        }
        if location.app_id in app_by_id:
            item["app_id"] = location.app_id
            item["app_name"] = str(app_by_id[location.app_id].name or "")[:256]
            child_terms.setdefault(location.app_id, []).append(name)
            child_tracks.setdefault(location.app_id, []).append(item)
        else:
            documents.append((track.id, name + " " + purpose, item))

    # A relevant Track can identify its owning App even when the App's own
    # description uses different vocabulary. Only already-admitted Track names
    # participate, and the bounded text never includes Entry data.
    documents = [
        (
            (
                identity,
                text + " " + " ".join(sorted(child_terms.get(identity, [])))[:2048],
                item,
            )
            if item["kind"] == "app"
            else (identity, text, item)
        )
        for identity, text, item in documents
    ]

    from app.agentive.harness.capability_search import _rank, _tool_text

    lexical = _rank(query, [(identity, text) for identity, text, _ in documents])
    ranks = {
        identity: 1.0 / (60 + rank) for rank, (identity, _) in enumerate(lexical, 1)
    }
    mode = "lexical"

    async def semantic_ranks():
        from app.agentive.harness.capability_search import (
            _definition_vector,
            _embed_text_for_capability_search,
        )

        vector = await _embed_text_for_capability_search(query)
        if not vector or not all(math.isfinite(v) for v in vector):
            raise ValueError("Invalid query embedding")
        magnitude = math.sqrt(sum(v * v for v in vector)) or 1.0
        scores = []
        for identity, text, _ in documents:
            v = await _definition_vector(text)
            if len(v) != len(vector) or not all(math.isfinite(x) for x in v):
                raise ValueError("Incompatible resource embedding")
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            score = sum(a * b for a, b in zip(vector, v)) / (magnitude * norm)
            if score > 0:
                scores.append((identity, score))
        return sorted(scores, key=lambda pair: (-pair[1], pair[0]))

    if documents and ranking_mode != "lexical":
        try:
            semantic = await asyncio.wait_for(semantic_ranks(), timeout=2.0)
            for rank, (identity, _) in enumerate(semantic, 1):
                ranks[identity] = ranks.get(identity, 0.0) + 1.0 / (60 + rank)
            mode = "hybrid"
        except Exception as exc:
            # A missing, busy or cold encoder must not make resource discovery
            # unavailable. No partial semantic rankings are treated as complete.
            logger.warning(
                "Workspace resource semantic search unavailable: %s", type(exc).__name__
            )
            if ranking_mode == "hybrid":
                raise BadRequestError(
                    message="Semantic resource ranking is unavailable; restart the search with lexical mode"
                ) from None
            mode = "lexical"
    by_id = {identity: item for identity, _, item in documents}
    # The model needs a browseable hierarchy, including relevant resources
    # whose words do not overlap the request. Keyword relevance orders the
    # inventory; it must not silently eliminate legitimate candidates.
    ordered = sorted(by_id, key=lambda identity: (-ranks.get(identity, 0), identity))
    candidates = [by_id[identity] for identity in ordered[offset : offset + limit]]
    selected: list[dict[str, Any]] = []
    used_bytes = 0
    for item in candidates:
        if item["kind"] != "app":
            while len(json.dumps(item, default=str).encode("utf-8")) > 3000:
                if item["description"]:
                    item["description"] = item["description"][
                        : len(item["description"]) // 2
                    ]
                    item["description_incomplete"] = True
                elif len(item["name"]) > 1:
                    item["name"] = item["name"][: len(item["name"]) // 2]
                    item["name_incomplete"] = True
                else:
                    break
            size = len(json.dumps(item, default=str).encode("utf-8")) + 2
            if selected and used_bytes + size > 7000:
                break
            selected.append(item)
            used_bytes += size
            continue
        children = child_tracks.get(item["id"], [])
        ranked_children = _rank(
            query,
            [
                (child["id"], child["name"] + " " + child["description"])
                for child in children
            ],
        )
        child_order = {
            identity: index for index, (identity, _) in enumerate(ranked_children)
        }
        children = sorted(
            children,
            key=lambda child: (
                child_order.get(child["id"], len(child_order)),
                child["name"],
                child["id"],
            ),
        )
        item["tracks"] = children[:8]
        item["tracks_incomplete"] = len(children) > 8
        catalogue = await list_workspace_tools(
            user_id=user_id,
            workspace_id=workspace_id,
            app_id=item["id"],
            read_only=True,
            limit=50,
        )
        # Do not silently cut a tool schema in half; the full live catalogue is
        # available through the existing governed discovery service.
        available_tools = catalogue.get("tools", [])
        ranked = _rank(
            query,
            [
                (
                    str(tool.get("key", "")),
                    _tool_text(
                        {
                            "name": tool.get("name") or tool.get("key"),
                            "description": tool.get("description"),
                            "input_schema": tool.get("parameters_schema") or {},
                        },
                        include_schema_details=True,
                    ),
                )
                for tool in available_tools
            ],
        )
        tool_order = {key: index for index, (key, _) in enumerate(ranked)}
        available_tools = sorted(
            available_tools,
            key=lambda tool: (
                tool_order.get(str(tool.get("key", "")), len(tool_order)),
                str(tool.get("key", "")),
            ),
        )
        tools = []
        for tool in available_tools[:3]:
            if len(json.dumps(tool, default=str).encode()) <= 4096:
                tools.append(tool)
        item["read_tools"] = tools
        item["tools_incomplete"] = bool(catalogue.get("has_more")) or len(tools) != len(
            available_tools
        )
        # Preserve complete descriptors within the active-turn preview budget.
        while len(json.dumps(item, default=str).encode("utf-8")) > 3000:
            if len(item["read_tools"]) > 1:
                item["read_tools"].pop()
                item["tools_incomplete"] = True
            elif len(item["tracks"]) > 2:
                item["tracks"].pop()
                item["tracks_incomplete"] = True
            elif item["read_tools"]:
                item["read_tools"].pop()
                item["tools_incomplete"] = True
            elif item["tracks"]:
                item["tracks"].pop()
                item["tracks_incomplete"] = True
            elif item["description"]:
                item["description"] = item["description"][
                    : len(item["description"]) // 2
                ]
                item["description_incomplete"] = True
            elif len(item["name"]) > 1:
                item["name"] = item["name"][: len(item["name"]) // 2]
                item["name_incomplete"] = True
            else:
                break
        size = len(json.dumps(item, default=str).encode("utf-8")) + 2
        if selected and used_bytes + size > 7000:
            break
        selected.append(item)
        used_bytes += size
    return {
        "results": selected,
        "mode": mode,
        "degraded": ranking_mode == "auto" and mode != "hybrid",
        "has_more": len(ordered) > offset + len(selected),
        "next_offset": (
            offset + len(selected) if len(ordered) > offset + len(selected) else None
        ),
        "guidance": "Metadata is a discovery aid, not record evidence or permission. For later pages pass this mode as ranking_mode; changes between pages can affect ordering. Use declared read tools and check live results.",
    }
