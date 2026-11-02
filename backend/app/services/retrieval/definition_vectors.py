"""Primary-store reuse of immutable metadata vectors across workers and restarts.

The native ToolSearch strategy callback owns ranking. Pydantic AI provides no
substrate persistence for derived vectors; this bridge supplies that missing
storage using jvspatial Objects. No plaintext, query, membership, result or
permission is persisted here. Callers supply currently admitted definitions.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from datetime import timedelta

from jvspatial.core.context import get_default_context

from app.models.definition_vector import DefinitionVector
from app.utils.time import utc_now, utc_now_iso

MAX_DEFINITIONS = 5000
READ_BATCH = 250
MAX_VECTOR_COMPONENTS = 2_000_000
MAX_CACHED_COMPONENTS = 200_000


def vector_identity(space: str, text: str) -> tuple[str, str]:
    """Derive a stable Object identity from encoder space and source digest."""
    digest = hashlib.sha256(text.encode()).hexdigest()
    key = hashlib.sha256(f"{space}:{digest}".encode()).hexdigest()
    return f"o.DefinitionVector.{key}", digest


def valid_vector(value: object) -> bool:
    """Accept only bounded finite numeric vectors, never booleans or NaNs."""
    return (
        isinstance(value, (list, tuple))
        and 0 < len(value) <= 8192
        and all(
            isinstance(item, (int, float))
            and not isinstance(item, bool)
            and math.isfinite(item)
            for item in value
        )
    )


async def read_vectors(
    space: str, texts: Sequence[str], *, dimensions: int | None = None
) -> dict[str, list[float]]:
    """Read only requested content identities in bounded primary-store batches."""
    if len(texts) > MAX_DEFINITIONS:
        raise ValueError("Metadata derivation batch exceeds the discovery budget")
    identities = {vector_identity(space, text)[0]: text for text in texts}
    ids = list(identities)
    database = get_default_context().database
    result = {}
    for start in range(0, len(ids), READ_BATCH):
        batch = ids[start : start + READ_BATCH]
        rows = await database.find(
            "object",
            {"entity": "DefinitionVector", "id": {"$in": batch}},
            limit=len(batch),
        )
        for row in rows:
            text = identities.get(row.get("id"))
            context = row.get("context")
            if text is None or not isinstance(context, dict):
                continue
            if (
                row.get("entity") == "DefinitionVector"
                and context.get("space") == space
                and context.get("digest") == vector_identity(space, text)[1]
                and valid_vector(context.get("vector"))
                and (dimensions is None or len(context["vector"]) == dimensions)
            ):
                result[text] = list(context["vector"])
    return result


async def save_vector(space: str, text: str, vector: list[float]) -> None:
    """Persist one immutable derivation; changing source/encoder changes its ID."""
    if not valid_vector(vector):
        raise ValueError("Invalid metadata embedding")
    identity, digest = vector_identity(space, text)
    await DefinitionVector(
        id=identity,
        space=space,
        digest=digest,
        vector=list(vector),
        created_at=utc_now_iso(),
    ).save()


async def prune_definition_vectors(*, limit: int = 100) -> int:
    """Bound retention work in the existing indexing worker, never a new daemon."""
    if not 1 <= limit <= 100:
        raise ValueError("Invalid metadata retention batch")
    cutoff = (utc_now() - timedelta(days=30)).isoformat()
    database = get_default_context().database
    query = {
        "entity": "DefinitionVector",
        "context.created_at": {"$lt": cutoff},
    }
    removed = 0
    for _ in range(limit):
        # Conditional deletion keeps a newly re-derived value out of this sweep.
        # A backend without atomic delete can only discard a recomputable cache.
        row = await database.find_one_and_delete("object", query)
        if row is None:
            break
        removed += 1
    return removed
