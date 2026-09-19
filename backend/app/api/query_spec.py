"""Authenticated broker-owned QuerySpec HTTP surface."""

from __future__ import annotations

import json
from typing import Any, Dict

from fastapi import Request
from jvspatial.api import endpoint
from pydantic import ValidationError

from app.api.errors import BadRequestError, MissingAuthenticationError
from app.api.utils import resolve_principal_id
from app.schemas.query_spec import QuerySpec, QuerySpecResult
from app.services.request_scope import resolve_workspace_id_from_request


@endpoint("/query-spec", methods=["POST"], auth=True, tags=["Query"])
async def execute_query_spec_endpoint(request: Request) -> Dict[str, Any]:
    """Execute a bounded Core query exclusively through the capability broker."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    workspace_id = await resolve_workspace_id_from_request(request, user_id)
    try:
        raw = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BadRequestError(message="Malformed JSON request body") from exc
    try:
        QuerySpec.model_validate(raw)
    except ValidationError as exc:
        raise BadRequestError(
            message="Invalid QuerySpec",
            details={"errors": exc.errors(include_url=False)},
        ) from exc

    from app.agentive.services import capability_broker

    result = await capability_broker.invoke_declared_capability(
        principal_id=user_id,
        workspace_id=workspace_id or "",
        capability_key="integral_query_spec",
        origin="http",
        source="core",
        op_class="read",
        arguments={"spec": raw},
        idempotency_key=request.headers.get("idempotency-key"),
    )
    if not result.ok:
        raise BadRequestError(
            message=result.message or "QuerySpec execution failed",
            details={"error_code": result.error_code},
        )
    payload = dict(result.data or {})
    payload["receipt"] = result.receipt.model_dump() if result.receipt else None
    return QuerySpecResult.model_validate(payload).model_dump(mode="json")
