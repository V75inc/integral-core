"""Service-layer helper that raises a uniform conflict error on duplicates.

Endpoints call :func:`assert_unique` after Pydantic input validation but before
persisting a new node or saving a rename. The helper queries the requested
Node class with the supplied ``context.<field>`` filter, optionally excludes a
self-id (so a no-op rename does not collide with itself), and raises
:class:`ResourceConflictError` with a per-entity ``error_code``.

The query MUST already key off a fold column (``name_fold`` / ``title_fold``)
so case- and whitespace-only differences collide. Callers compute the fold
value via :func:`app.api.validators_common.compute_fold`.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Type

# This service is imported by graph/application services during ordinary runtime
# construction.  Import the framework error directly so importing a service does
# not execute ``app.api``'s endpoint-registration package initializer.  The API
# error facade re-exports this exact class, so callers retain one response type.
from jvspatial.api.exceptions import ResourceConflictError


async def assert_unique(
    node_cls: Type[Any],
    query: Dict[str, Any],
    *,
    entity: str,
    field_label: str,
    value: str,
    scope_label: str,
    exclude_id: Optional[str] = None,
) -> None:
    """Raise ResourceConflictError when ``query`` matches an existing node.

    Args:
        node_cls: jvspatial Node class (e.g. ``Workspace``, ``App``).
        query: ``{"context.<field>": ...}`` filter passed to ``Node.find``.
        entity: short snake_case entity name used in ``error_code``
            (e.g. ``"workspace"`` → ``error_code="resource.duplicate_workspace"``).
        field_label: human-readable field name for the message (e.g. ``"name"``).
        value: the raw value the caller supplied (used in the error message).
        scope_label: short phrase describing the uniqueness scope
            (e.g. ``"in this workspace"`` or ``"under your account"``).
        exclude_id: node id to ignore in match results (used by update handlers
            so renaming a node to its own current value is a no-op).
    """
    matches = await node_cls.find(query)
    if not matches:
        return
    if exclude_id is not None:
        matches = [m for m in matches if getattr(m, "id", None) != exclude_id]
        if not matches:
            return
    err = ResourceConflictError(
        message=(
            f"{entity.replace('_', ' ').capitalize()} with {field_label} "
            f"'{value}' already exists {scope_label}"
        ),
        details={
            "entity": entity,
            "field": field_label,
            "value": value,
            "conflict_id": getattr(matches[0], "id", None),
        },
    )
    err.error_code = f"resource.duplicate_{entity}"
    raise err
