"""The values a column of a Track list actually holds, with how many records have each.

A filter menu offers these as a pick-list (so nobody types "Engineering" and hopes), for text, choice and link
columns alike. Only records the caller may read are counted, the other active filters apply (so a count says how
many would remain), and the scan is bounded: a very large list reports that it stopped early instead of stalling.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List, Optional

from app.models.nodes import Track
from app.services.entry_access_projection import candidate_query, iter_records
from app.services.entry_listing import _resolve_view_type_ids, view_filter_clauses
from app.services.pagination import _context_field_query_path, _merge_query_clauses

SCAN_LIMIT = 20_000
_PATH = re.compile(r"^(title|(custom_fields\.)?[A-Za-z][A-Za-z0-9_]{0,127})$")


def _value_of(row: Dict[str, Any], path: str) -> Any:
    node: Any = row
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


async def facet_values(
    user_id: str,
    track_id: str,
    field: str,
    *,
    filters: Optional[List[Dict[str, Any]]] = None,
    view_node: Any = None,
    q: str = "",
    limit: int = 50,
) -> Dict[str, Any]:
    """``{"values": [{"value", "count"}], "truncated": bool, "distinct": n}`` for one field, most common first."""
    from app.api.errors import BadRequestError

    if not _PATH.match(field or ""):
        raise BadRequestError(message="Invalid field")
    path = _context_field_query_path(field)
    track = await Track.get(track_id)
    if track is None:
        return {"values": [], "truncated": False, "distinct": 0}

    scope = {"track_id": track_id, "app_id": None, "workspace_id": None}
    scope_query = await candidate_query(user_id, **scope)
    if scope_query is None:
        return {"values": [], "truncated": False, "distinct": 0}
    clauses: List[Dict[str, Any]] = [scope_query] if scope_query else []
    if view_node is not None:
        type_ids = await _resolve_view_type_ids(view_node)
        if type_ids is not None:
            if not type_ids:
                return {"values": [], "truncated": False, "distinct": 0}
            clauses.append({"context.type_id": {"$in": list(type_ids)}})
        cfg = getattr(view_node, "config", None) or {}
        if isinstance(cfg, dict):
            clauses.extend(view_filter_clauses(cfg.get("filters")))
    # The other filters narrow what is counted; this column's own filter is left out by the caller.
    clauses.extend(view_filter_clauses(filters or []))
    query = _merge_query_clauses(*clauses)

    counts: Counter = Counter()
    scanned = 0
    truncated = False
    async for records in iter_records(user_id, query, track_id=track_id, projection=[path]):
        for row in records:
            raw = _value_of(row, path)
            for item in raw if isinstance(raw, list) else [raw]:
                if isinstance(item, (str, int, float, bool)) and item != "":
                    counts[item] += 1
            scanned += 1
        if scanned >= SCAN_LIMIT:
            truncated = True
            break

    needle = (q or "").strip().lower()
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0]).lower()))
    if needle:
        ordered = [kv for kv in ordered if needle in str(kv[0]).lower()]
    return {
        "values": [{"value": value, "count": count} for value, count in ordered[: max(1, min(limit, 200))]],
        "truncated": truncated,
        "distinct": len(counts),
    }
