"""W2.3: when ranking has no winner, name the smallest structure to add.

Does not invent a destination. Empty workspace → new App. Tracks exist but
none share schema labels → new Track on an existing App. A track resembles
the note but cannot hold it → new EntryType on that track. The note is
returned as ``preserve`` so it can be filed once after that structure lands.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def classify_no_fit(
    facet: Dict[str, Any],
    *,
    open_track_count: int,
) -> Optional[Dict[str, Any]]:
    """Return a route when this facet has no winner. None when it can file."""
    if facet.get("winner"):
        return None
    preserved = {
        "text": facet.get("text") or "",
        "fields": dict(facet.get("fields") or {}),
    }
    if open_track_count <= 0:
        return {
            "kind": "new_app",
            "via": "scaffold",
            "why": "there is no open track to file into",
            "preserve": preserved,
        }
    candidates = [
        row for row in (facet.get("candidates") or []) if isinstance(row, dict)
    ]
    close = [row for row in candidates if (row.get("schema_fit") or 0) > 0]
    if close:
        best = close[0]
        return {
            "kind": "new_entry_type",
            "via": "entry_type",
            "app_id": best.get("app_id"),
            "track_id": best.get("track_id"),
            "track_title": best.get("track_title"),
            "why": (
                f"{best.get('track_title') or 'this track'} resembles the note "
                "but has no entry type that can hold it"
            ),
            "preserve": preserved,
        }
    host = candidates[0] if candidates else None
    return {
        "kind": "new_track",
        "via": "create_app_track",
        "app_id": (host or {}).get("app_id"),
        "app_name": (host or {}).get("app_name"),
        "why": "open tracks exist but none share schema labels with this note",
        "preserve": preserved,
    }


def attach_no_fit_routes(
    ranked: Dict[str, Any],
    *,
    open_track_count: int,
) -> Dict[str, Any]:
    """Add ``route`` to each no-fit facet. Leaves winners untouched."""
    if ranked.get("error"):
        return ranked
    facets = []
    for facet in ranked.get("facets") or []:
        if not isinstance(facet, dict):
            continue
        row = dict(facet)
        route = classify_no_fit(row, open_track_count=open_track_count)
        if route is not None:
            row["route"] = route
        facets.append(row)
    ranked = dict(ranked)
    ranked["facets"] = facets
    return ranked
