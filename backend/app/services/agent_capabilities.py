"""Resident/MCP helpers for capability catalogue + governed query (ADR-012)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


async def describe_capabilities(
    *,
    user_id: str,
    workspace_id: str,
    include_paused: bool = False,
) -> Dict[str, Any]:
    """Return permission-filtered capability catalogue for the resident/MCP."""
    from app.services.capability_catalogue import (
        filter_capabilities_for_principal,
        get_or_compile_catalogue,
    )
    from app.services.permissions import resolve_role

    snap = await get_or_compile_catalogue(workspace_id)
    caps = filter_capabilities_for_principal(snap, include_paused=include_paused)
    visible: List[Dict[str, Any]] = []
    for c in caps:
        if c.app_id and await resolve_role(user_id, "app", c.app_id) is None:
            continue
        visible.append(c.model_dump())
    return {
        "workspace_id": workspace_id,
        "generation_id": snap.generation_id,
        "capabilities": visible,
    }


async def governed_query(
    *,
    user_id: str,
    workspace_id: str,
    mode: str,
    capability_key: Optional[str] = None,
    app_id: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
    resource: Optional[str] = None,
    filters: Optional[List[Dict[str, Any]]] = None,
    projection: Optional[List[str]] = None,
    limit: int = 50,
    cursor: Optional[str] = None,
    max_depth: int = 1,
    retrieval_mode: str = "deterministic",
    catalogue_generation: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute a governed QuerySpec on behalf of the resident/MCP."""
    from app.schemas.governed_query import FilterExpr, QuerySpec
    from app.services.governed_query import execute_query

    spec_kwargs: Dict[str, Any] = {
        "mode": mode,
        "limit": limit,
        "cursor": cursor,
        "max_depth": max_depth,
        "retrieval_mode": retrieval_mode,
    }
    if mode == "declared_capability":
        spec_kwargs["capability_key"] = capability_key
        spec_kwargs["app_id"] = app_id
        spec_kwargs["params"] = dict(params or {})
    else:
        spec_kwargs["resource"] = resource
        spec_kwargs["filters"] = [FilterExpr.model_validate(f) for f in (filters or [])]
        spec_kwargs["projection"] = list(projection or [])

    spec = QuerySpec.model_validate(spec_kwargs)
    result = await execute_query(
        user_id=user_id,
        workspace_id=workspace_id,
        spec=spec,
        catalogue_generation=catalogue_generation,
    )
    return result.model_dump()
