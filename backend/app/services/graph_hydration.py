"""Batch graph hydration via jvspatial GraphContext.get_batch / bulk_save."""

from __future__ import annotations

from typing import Dict, List, Type, TypeVar

from jvspatial.core.context import get_default_context
from jvspatial.core.entities.node import Node

T = TypeVar("T", bound=Node)


async def batch_get_by_ids(
    entity_class: Type[T],
    ids: List[str],
) -> Dict[str, T]:
    """Hydrate nodes by id using GraphContext.get_batch (identity-map aware)."""
    unique = list(dict.fromkeys(i for i in ids if i))
    if not unique:
        return {}
    ctx = get_default_context()
    nodes = await ctx.get_batch(entity_class, unique)
    out: Dict[str, T] = {}
    for node in nodes:
        nid = getattr(node, "id", None)
        if nid:
            out[nid] = node
    return out


async def bulk_save_nodes(nodes: List[Node]) -> int:
    """Persist many Node instances via jvspatial ``GraphContext.save_batch``.

    ``save_batch`` prepares entity records and uses bounded native writes by
    collection. It does not call overridden entity ``save`` hooks and is not
    atomic across collections; callers needing those contracts must supply
    their own hook execution and graph transaction. The DB-level
    ``bulk_save``/``bulk_save_detailed`` take
    ``(collection, records)`` — raw record dicts, not Node objects — so they
    are the wrong primitive here.
    """
    if not nodes:
        return 0
    ctx = get_default_context()
    saved = await ctx.save_batch(nodes)
    return len(saved)
