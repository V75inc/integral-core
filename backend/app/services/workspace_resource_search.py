"""Bounded discovery of currently accessible workspace resource metadata.

A durable metadata projection selects candidates. Live canonical containment
and policy still admit every disclosed resource; metadata is never a grant.
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


def _validate_search(workspace_id, query, limit, offset, ranking_mode):
    """Validate the common discovery boundary before accessing a source."""
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


async def discover_workspace_resources(
    *,
    user_id: str,
    workspace_id: str | None = None,
    query: str,
    limit: int = 6,
    offset: int = 0,
    ranking_mode: str = "lexical",
) -> dict[str, Any]:
    """Use incremental candidates when ready, otherwise the canonical inventory."""
    # Reuse the same validation before any index intent or primary reads.
    _validate_search(workspace_id, query, limit, offset, ranking_mode)
    from app.models.nodes import App
    from app.services.resource_metadata_index import indexed_inventory

    indexed = await indexed_inventory(
        user_id=user_id,
        workspace_id=workspace_id,
        query=query,
        offset=offset,
        limit=limit,
        ranking_mode=ranking_mode,
    )
    if indexed is None:
        result = await _discover_workspace_resources(
            user_id=user_id,
            workspace_id=workspace_id,
            query=query,
            limit=limit,
            offset=offset,
            ranking_mode=ranking_mode,
        )
        result["index_coverage"] = {
            "kind": "canonical_fallback",
            "projection_pending": True,
        }
        return result
    app_ids = {item.id for item in indexed["resources"] if isinstance(item, App)}
    resource_ids = {item.id for item in indexed["resources"]}
    tracks = [
        item
        for item in indexed["tracks"]
        if item.id in resource_ids or indexed["parents"].get(item.id) in app_ids
    ]
    result = await _discover_workspace_resources(
        user_id=user_id,
        workspace_id=workspace_id,
        query=query,
        limit=limit,
        offset=0,
        ranking_mode="lexical",
        inventory={
            "apps": [item for item in indexed["resources"] if isinstance(item, App)],
            "tracks": tracks,
            "resource_order": [item.id for item in indexed["resources"]],
        },
    )
    for item in result["results"]:
        if item["kind"] == "app":
            # The indexed page carries matching children, not a full child inventory.
            item["tracks_incomplete"] = True
    result.update(
        mode=indexed["mode"],
        degraded=ranking_mode == "auto" and indexed["mode"] != "hybrid",
        has_more=indexed["has_more"]
        or len(result["results"]) < len(indexed["resources"]),
        index_coverage=indexed["coverage"],
    )
    result["next_offset"] = (
        offset + max(1, len(result["results"])) if result["has_more"] else None
    )
    return result


def _structured_read_contract(contract: dict) -> bool:
    properties = (contract.get("input_schema") or {}).get("properties") or {}
    return {"track", "filters", "count_only", "group_by"}.issubset(properties)


def _preview_read_contracts(contracts: list[dict]) -> list[dict]:
    """Reserve a reusable structured read alongside intent-ranked reports."""
    selected = list(contracts[:3])
    structured = next(
        (item for item in contracts if _structured_read_contract(item)), None
    )
    if structured is not None and structured not in selected:
        selected = selected[:2] + [structured]
    bounded = []
    for original in selected:
        item = dict(original)
        if len(json.dumps(item).encode()) > 4096 and "output_schema" in item:
            if item.pop("output_schema"):
                item["output_schema_omitted"] = True
        item["input_schema_complete"] = True
        if len(json.dumps(item).encode()) <= 4096:
            bounded.append(item)
    return bounded


def _shared_read_bindings(contracts: list[dict]) -> tuple[dict, list[dict]]:
    """Encode repeated invocation defaults once without changing any schema."""
    if not contracts:
        return {}, []
    first = contracts[0].get("arguments") or {}
    shared = {
        key: value
        for key, value in first.items()
        if key in {"mode", "app_id", "catalogue_generation"}
        and all(
            (contract.get("arguments") or {}).get(key) == value
            for contract in contracts
        )
    }
    compact = []
    for original in contracts:
        contract = dict(original)
        if shared:
            remaining = {
                key: value
                for key, value in (original.get("arguments") or {}).items()
                if key not in shared
            }
            if remaining == {"capability_key": original.get("capability_key")}:
                contract.pop("arguments", None)
            else:
                contract["arguments"] = remaining
        compact.append(contract)
    return shared, compact


async def _discover_workspace_resources(
    *,
    user_id: str,
    workspace_id: str | None = None,
    query: str,
    limit: int = 6,
    offset: int = 0,
    ranking_mode: str = "lexical",
    inventory: dict | None = None,
) -> dict[str, Any]:
    """Rank Apps and Tracks, then disclose only currently authorized metadata."""
    _validate_search(workspace_id, query, limit, offset, ranking_mode)

    apps = (
        inventory["apps"]
        if inventory is not None
        else await callable_apps_for_scope(user_id, workspace_id=workspace_id)
    )
    tracks = (
        inventory["tracks"]
        if inventory is not None
        else await accessible_tracks_for_scope(user_id, workspace_id=workspace_id)
    )
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
            child_terms.setdefault(location.app_id, []).append(name + " " + purpose)
            child_tracks.setdefault(location.app_id, []).append(item)
        else:
            documents.append((track.id, name + " " + purpose, item))

    # A relevant Track can identify its owning App even when the App's own
    # description uses different vocabulary. Only already-admitted Track metadata
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
            _definition_vectors,
            _embed_text_for_capability_search,
        )

        vector = await _embed_text_for_capability_search(query)
        if not vector or not all(math.isfinite(v) for v in vector):
            raise ValueError("Invalid query embedding")
        magnitude = math.sqrt(sum(v * v for v in vector)) or 1.0
        scores = []
        vectors = await _definition_vectors([text for _, text, _ in documents])
        for (identity, _text, _), v in zip(documents, vectors):
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
    if inventory is not None and "resource_order" in inventory:
        # The incremental index already ranked admitted resources using Track
        # purposes and optional semantic similarity. Formatting a narrower
        # shortlist must not replace that ordering with another lexical score.
        # Current containment and policy above still filter every descriptor.
        ordered = [
            identity for identity in inventory["resource_order"] if identity in by_id
        ]
    candidates = [by_id[identity] for identity in ordered[offset : offset + limit]]
    from app.services.agent_capabilities import app_read_contracts

    read_contracts = await app_read_contracts(
        workspace_id=workspace_id,
        app_ids=[item["id"] for item in candidates if item["kind"] == "app"],
    )
    selected: list[dict[str, Any]] = []
    used_bytes = 0
    for item in candidates:
        if item["kind"] != "app":
            while len(json.dumps(item, default=str).encode("utf-8")) > 4096:
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
        from app.services.walkers.resource_schema_preview import app_schema_preview

        preview = await app_schema_preview(
            app_id=item["id"], user_id=user_id, workspace_id=workspace_id, query=query
        )
        item["schema_preview"] = preview
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
        queries = read_contracts.get(item["id"], [])
        ranked_queries = _rank(
            query,
            [
                (item["capability_key"], item["name"] + " " + item["description"])
                for item in queries
            ],
        )
        query_order = {key: index for index, (key, _) in enumerate(ranked_queries)}
        queries = sorted(
            queries,
            key=lambda item: (
                query_order.get(item["capability_key"], len(query_order)),
                item["capability_key"],
            ),
        )
        shared, selected_queries = _shared_read_bindings(
            _preview_read_contracts(queries)
        )
        if shared:
            item["query_invoke_args"] = shared
        item["read_queries"] = selected_queries
        item["queries_incomplete"] = len(item["read_queries"]) != len(queries)
        item["tools_incomplete"] = bool(catalogue.get("has_more")) or len(tools) != len(
            available_tools
        )
        # Preserve complete descriptors within the active-turn preview budget.
        while len(json.dumps(item, default=str).encode("utf-8")) > 6144:
            if item["read_tools"]:
                item["read_tools"].pop()
                item["tools_incomplete"] = True
            elif len(item["description"]) > 128:
                item["description"] = item["description"][
                    : len(item["description"]) // 2
                ]
            elif len(item["schema_preview"]["tracks"]) > 1:
                item["schema_preview"]["tracks"].pop()
                item["schema_preview"]["incomplete"] = True
            elif item["tracks"]:
                item["tracks"].pop()
                item["tracks_incomplete"] = True
            elif any("output_schema" in contract for contract in item["read_queries"]):
                contract = next(
                    contract
                    for contract in reversed(item["read_queries"])
                    if "output_schema" in contract
                )
                if contract.pop("output_schema"):
                    contract["output_schema_omitted"] = True
            elif item["schema_preview"]["tracks"]:
                # The bounded hierarchy is needed to discover the right source;
                # detailed field previews can be fetched through native tools.
                item["schema_preview"]["tracks"].pop()
                item["schema_preview"]["incomplete"] = True
            elif len(item["read_queries"]) > 1:
                # Losing a general count/filter contract caused fallback
                # discovery and repeated sample reads in the browser smoke.
                removable = [
                    i
                    for i, contract in enumerate(item["read_queries"])
                    if not _structured_read_contract(contract)
                ]
                item["read_queries"].pop(removable[-1] if removable else -1)
                item["queries_incomplete"] = True
            elif any(
                row.get("purpose")
                for row in item["schema_preview"].get("directory", [])
            ):
                # Keep the hierarchy intact before omitting optional meaning.
                row = next(
                    row
                    for row in reversed(item["schema_preview"]["directory"])
                    if row.get("purpose")
                )
                row.pop("purpose")
                row["purpose_incomplete"] = True
            elif item["schema_preview"].get("directory"):
                item["schema_preview"]["directory"].pop()
                item["schema_preview"]["directory_incomplete"] = True
            elif item["read_queries"]:
                item["read_queries"].pop()
                item["queries_incomplete"] = True
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
        "guidance": "Metadata is a discovery aid, not record evidence or permission. For later pages pass this mode as ranking_mode; changes between pages can affect ordering. Prefer a matching read_queries contract with a complete input schema with integral_governed_query over a tool alias for the same read. Use its supplied exact key, App ID and generation; check live results. The Track directory identifies real source choices. App descriptions do not prove an entry type exists; invalid selections are not evidence of zero records.",
    }
