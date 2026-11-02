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
    sort: Optional[str] = None,
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
        spec_kwargs["sort"] = sort

    spec = QuerySpec.model_validate(spec_kwargs)
    result = await execute_query(
        user_id=user_id,
        workspace_id=workspace_id,
        spec=spec,
        catalogue_generation=catalogue_generation,
    )
    return result.model_dump()


async def app_read_contracts(
    *, workspace_id: str, app_ids: list[str]
) -> dict[str, list[dict]]:
    """Bundle live declared read schemas for already-admitted discovery Apps.

    This internal bridge does not authorize Apps: callers must admit every App
    first. Execution still requires the broker and current catalogue generation.
    """
    from app.services.app_queries.registry import all_workspace_query_apps
    from app.services.capability_catalogue import get_or_compile_catalogue

    selected = set(app_ids)
    if not any(app_id in selected for app_id in all_workspace_query_apps(workspace_id)):
        return {}
    snapshot = await get_or_compile_catalogue(workspace_id)
    result: dict[str, list[dict]] = {}
    for capability in snapshot.capabilities:
        if (
            capability.app_id not in selected
            or capability.kind != "query"
            or capability.effects != "read"
            or capability.availability != "active"
            or not capability.discoverable
        ):
            continue
        result.setdefault(capability.app_id, []).append(
            {
                "capability_key": capability.capability_id,
                "name": capability.name,
                "description": capability.description,
                "input_schema": capability.input_schema,
                "output_schema": capability.output_schema,
                "invoke_with": "integral_governed_query",
                "arguments": {
                    "mode": "declared_capability",
                    "app_id": capability.app_id,
                    "capability_key": capability.capability_id,
                    "catalogue_generation": snapshot.generation_id,
                },
            }
        )
    for contracts in result.values():
        contracts.sort(
            key=lambda item: not {
                "track",
                "filters",
                "count_only",
                "group_by",
            }.issubset((item.get("input_schema") or {}).get("properties") or {})
        )
    return result
