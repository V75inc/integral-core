"""Idempotent edge creation helpers.

Several service-layer paths call ``source.connect(target, edge=X, ...)`` without
first checking whether the edge already exists. Under concurrent writes (and
even under sequential retries) that yields duplicate edges with the same
semantics. :func:`ensure_edge` reads the existing edge set, returns the first
match if present, and only creates a new edge otherwise.

The helper is best-effort: it narrows the duplicate-edge window but does not
provide a true atomic upsert. Concurrent writers can still race between the
read and the connect; that is acceptable pre-1.0 — the priority here is to
stop the easy double-call bug that produced duplicates in normal use.
"""

from __future__ import annotations

from typing import Any, Optional


async def ensure_edge(
    source: Any,
    target: Any,
    edge_class: Any,
    **attrs: Any,
) -> Any:
    """Return existing edge or create a new one between two nodes.

    Looks up an edge of type ``edge_class`` between ``source`` and ``target``;
    if none exists, creates one with ``attrs`` and returns it.
    """
    ctx = await source.get_context()
    existing = await ctx.find_edges_between(source.id, target.id, edge_class=edge_class)
    if existing:
        return existing[0]
    return await source.connect(target, edge=edge_class, **attrs)


async def find_existing_edge(
    source: Any,
    target: Any,
    edge_class: Any,
) -> Optional[Any]:
    """Return the first existing edge between two nodes, or None."""
    ctx = await source.get_context()
    existing = await ctx.find_edges_between(source.id, target.id, edge_class=edge_class)
    return existing[0] if existing else None
