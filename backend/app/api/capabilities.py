"""HTTP surface for capability catalogue + governed query (ADR-012)."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import Request
from jvspatial.api import endpoint

from app.api.errors import MissingAuthenticationError
from app.api.utils import resolve_principal_id
from app.schemas.governed_query import (
    CapabilitiesListResponse,
    QueryRequest,
    QueryResult,
)
from app.services.request_scope import resolve_execution_scope_from_request


@endpoint(
    "/capabilities",
    methods=["GET"],
    auth=True,
    tags=["Capabilities"],
)
async def list_capabilities(request: Request) -> Dict[str, Any]:
    """Permission-filtered capability catalogue for the active workspace."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    execution_scope = await resolve_execution_scope_from_request(
        request, user_id, origin="http_capabilities_list"
    )
    include_paused = str(request.query_params.get("include_paused") or "").lower() in (
        "1",
        "true",
        "yes",
    )
    from app.services.capability_catalogue import (
        filter_capabilities_for_principal,
        get_or_compile_catalogue,
    )
    from app.services.permissions import resolve_role

    snap = await get_or_compile_catalogue(execution_scope.workspace_id)
    caps = filter_capabilities_for_principal(snap, include_paused=include_paused)
    # Drop app-scoped caps the caller cannot see
    visible = []
    for c in caps:
        if c.app_id:
            if await resolve_role(user_id, "app", c.app_id) is None:
                continue
        visible.append(c.model_dump())
    return CapabilitiesListResponse(
        workspace_id=execution_scope.workspace_id,
        generation_id=snap.generation_id,
        capabilities=visible,
    ).model_dump()


@endpoint(
    "/query",
    methods=["POST"],
    auth=True,
    tags=["Capabilities"],
)
async def post_query(request: Request) -> Dict[str, Any]:
    """Execute a governed QuerySpec (declared App or open Core)."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    execution_scope = await resolve_execution_scope_from_request(
        request, user_id, origin="http_governed_query"
    )
    raw = await request.json() if request.method == "POST" else {}
    body = QueryRequest.model_validate(raw or {})
    generation = request.headers.get("X-Integral-Catalogue-Generation")
    from app.services.governed_query import execute_query

    result: QueryResult = await execute_query(
        user_id=execution_scope.principal_id,
        workspace_id=execution_scope.workspace_id,
        spec=body.query,
        catalogue_generation=generation,
    )
    return result.model_dump()


@endpoint(
    "/extensions/{app_id}/queries",
    methods=["GET"],
    auth=True,
    tags=["App Extensions"],
)
async def list_extension_queries(request: Request, app_id: str) -> Dict[str, Any]:
    """List declared App queries exposed to extension hosts."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    execution_scope = await resolve_execution_scope_from_request(
        request, user_id, origin="http_extension_queries_list"
    )
    from app.services.app_queries.dispatch import list_app_queries

    return await list_app_queries(
        user_id=execution_scope.principal_id,
        workspace_id=execution_scope.workspace_id,
        app_id=app_id,
    )


@endpoint(
    "/extensions/{app_id}/queries/{query_key}",
    methods=["POST"],
    auth=True,
    tags=["App Extensions"],
)
async def invoke_extension_query(
    request: Request, app_id: str, query_key: str
) -> Dict[str, Any]:
    """Invoke a declared App query from an extension host."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    execution_scope = await resolve_execution_scope_from_request(
        request, user_id, origin="http_extension_query"
    )
    raw = await request.json() if request.method == "POST" else {}
    params = (raw or {}).get("params") or (raw or {}).get("input") or {}
    correlation_id = request.headers.get("X-Correlation-Id")
    from app.schemas.capabilities import Evidence
    from app.services.app_queries.dispatch import invoke_app_query
    from app.services.governed_query.engine import _refs_from_output
    from app.utils.time import utc_now_iso

    invoked = await invoke_app_query(
        user_id=execution_scope.principal_id,
        workspace_id=execution_scope.workspace_id,
        app_id=app_id,
        query_key=query_key,
        params=params if isinstance(params, dict) else {},
        correlation_id=correlation_id,
    )
    output = dict(invoked.get("output") or {})
    refs = _refs_from_output(
        output, workspace_id=execution_scope.workspace_id, app_id=app_id
    )
    evidence = Evidence(
        object_refs=refs,
        freshness=utc_now_iso(),
        applied_scope=f"ws:{execution_scope.workspace_id}",
        policy_decision_id=invoked.get("policy_decision_id"),
        audit_correlation_id=correlation_id,
    )
    return {
        **invoked,
        "object_refs": [r.model_dump() for r in refs],
        "evidence": evidence.model_dump(),
    }
