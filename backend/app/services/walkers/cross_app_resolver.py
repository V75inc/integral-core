"""Cross-App inbound reference resolver (Phase 10 Plan 10-06).

Enumerates ``REFERENCES`` edges whose ``target_app_id`` equals a given App id —
i.e. find every source Entry that points INTO the target App via a cross-App
relation. Used by ``app_lifecycle.uninstall_app`` to perform the edge-level
walk (Pitfall 6, second of the two-walk uninstall check).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from jvspatial.core import Walker
from jvspatial.core.decorators import on_visit

from app.models.edges import CONTAINS, REFERENCES
from app.models.nodes import App, Entry, Track

logger = logging.getLogger(__name__)


class CrossAppInboundReferenceWalker(Walker):
    """Walker that enumerates inbound cross-App REFERENCES edges.

    Spawn from any Workspace's App registry; traverses App → CONTAINS → Track
    → CONTAINS → Entry. On each Entry visit, queries inbound REFERENCES edges
    and collects those whose ``target_app_id`` matches the configured
    ``target_app_id``.
    """

    target_app_id: str = ""
    results: List[Dict[str, Any]] = []

    @on_visit(App)
    async def on_app(self, here: App) -> None:
        if here.id == self.target_app_id:
            return
        if getattr(here, "lifecycle_state", "active") != "active":
            return
        tracks = await here.nodes(edge=[CONTAINS], node=["Track"])
        await self.visit(tracks)

    @on_visit(Track)
    async def on_track(self, here: Track) -> None:
        entries = await here.nodes(edge=[CONTAINS], node=["Entry"])
        await self.visit(entries)

    @on_visit(Entry)
    async def on_entry(self, here: Entry) -> None:
        ctx = await here.get_context()
        edges = await ctx.find_edges_between(
            here.id, target_id=None, edge_class=REFERENCES
        )
        for edge in edges:
            edge_target_app_id = getattr(edge, "target_app_id", None)
            if edge_target_app_id != self.target_app_id:
                continue
            self.results.append(
                {
                    "source_entry_id": here.id,
                    "source_track_id": here.track_id,
                    "relation_field_key": getattr(edge, "field_key", "") or "",
                }
            )


async def find_inbound_references(
    *,
    target_app_id: str,
    workspace_id: str,
    target_track_ids: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Enumerate REFERENCES edges with ``target_app_id == target_app_id``.

    Uses a bulk edge query + batched entry/app hydration instead of a
    per-track/per-entry procedural walk.
    """
    if not target_app_id:
        return []

    other_apps = await App.find({"workspace_id": workspace_id})
    other_apps = [
        a
        for a in other_apps
        if a.id != target_app_id and getattr(a, "lifecycle_state", "active") == "active"
    ]
    if not other_apps:
        return []

    other_app_ids = {a.id for a in other_apps}

    try:
        ctx = await other_apps[0].get_context()
        raw_edges = await ctx.database.find(
            "edge",
            {
                "entity": "REFERENCES",
                "context.target_app_id": target_app_id,
            },
        )
    except Exception as exc:
        logger.warning(
            "find_inbound_references: bulk edge query failed: %s — returning empty",
            exc,
        )
        return []

    if not raw_edges:
        return []

    source_ids = list(
        dict.fromkeys(
            str(e.get("source") or "").strip() for e in raw_edges if e.get("source")
        )
    )
    if not source_ids:
        return []

    entries = await Entry.find({"id": {"$in": source_ids}})
    entry_by_id = {e.id: e for e in entries if getattr(e, "id", None)}

    track_ids = list(
        dict.fromkeys(
            (getattr(e, "track_id", "") or "").strip()
            for e in entries
            if (getattr(e, "track_id", "") or "").strip()
        )
    )
    apps_by_track: Dict[str, List[App]] = {}
    if track_ids:
        try:
            apps_by_track = await Track.nodes_bulk(
                track_ids,
                direction="in",
                edge=["CONTAINS"],
                node=["WorkspaceApp"],
            )
        except Exception:
            apps_by_track = {}

    results: List[Dict[str, Any]] = []
    for edge in raw_edges:
        src_id = str(edge.get("source") or "").strip()
        entry = entry_by_id.get(src_id)
        if entry is None:
            continue
        track_id = (getattr(entry, "track_id", "") or "").strip()
        if target_track_ids and track_id and track_id not in target_track_ids:
            continue
        source_apps = apps_by_track.get(track_id) or []
        source_app = source_apps[0] if source_apps else None
        if source_app is None or source_app.id not in other_app_ids:
            continue
        ctx_edge = edge.get("context") or {}
        results.append(
            {
                "source_app_id": source_app.id,
                "source_app_name": source_app.name,
                "source_entry_id": entry.id,
                "source_track_id": track_id,
                "relation_field_key": str(
                    ctx_edge.get("field_key") or edge.get("field_key") or ""
                ),
            }
        )

    logger.info(
        "find_inbound_references: target_app=%s workspace=%s found=%d",
        target_app_id,
        workspace_id,
        len(results),
    )
    return results


__all__ = [
    "CrossAppInboundReferenceWalker",
    "find_inbound_references",
]
