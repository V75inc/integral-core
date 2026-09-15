"""Comment counts for entry API payloads."""

from collections import Counter
from typing import Any, Dict, List

from app.models.nodes import Entry


async def entry_comment_count(entry: Entry) -> int:
    """Count comments on this entry (same traversal as ``GET /entries/{id}/comments``)."""
    comments = await entry.nodes(edge=["HAS_COMMENT"], node=["Comment"])
    return len(comments)


async def attach_comment_count(entry_data: Dict[str, Any], entry: Entry) -> None:
    """Set ``comment_count`` on an exported entry dict."""
    entry_data["comment_count"] = await entry_comment_count(entry)


async def prefetch_comment_counts(entries: List[Entry]) -> Dict[str, int]:
    """Batch-count comments for a page of entries via a single edge query.

    Returns ``{entry_id: count}``.
    """
    if not entries:
        return {}
    entry_ids = [e.id for e in entries if getattr(e, "id", None)]
    if not entry_ids:
        return {}
    try:
        ctx = await entries[0].get_context()
        edges = await ctx.database.find(
            "edge",
            {"source": {"$in": entry_ids}, "entity": "HAS_COMMENT"},
        )
        counts = Counter(e.get("source") for e in edges)
        return dict(counts)
    except Exception:
        return {}


def apply_prefetched_comment_count(
    entry_data: Dict[str, Any],
    entry: Entry,
    counts: Dict[str, int],
) -> None:
    """Set ``comment_count`` from a prefetched counts dict."""
    entry_data["comment_count"] = counts.get(entry.id, 0)
