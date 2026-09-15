"""Free-text search helpers for Entry listings."""

from typing import Any, Dict, List, Optional

from app.models.nodes import Entry


def filter_entries_by_query(entries: List[Entry], q: Optional[str]) -> List[Entry]:
    """Return entries whose title or body contains ``q`` (case-insensitive).

    Returns the input list unchanged when ``q`` is empty/None.
    Legacy in-memory helper — prefer :func:`entry_search_query_clause` for DB lists.
    """
    if not q:
        return entries
    needle = q.casefold()
    return [
        e
        for e in entries
        if needle in (e.title or "").casefold() or needle in (e.body or "").casefold()
    ]


def entry_search_query_clause(q: Optional[str]) -> Dict[str, Any]:
    """Build a DB query clause for title/body substring search."""
    if not q or not str(q).strip():
        return {}
    needle = str(q).strip()
    return {
        "$or": [
            {"context.title": {"$regex": needle, "$options": "i"}},
            {"context.body": {"$regex": needle, "$options": "i"}},
        ]
    }
