"""Authenticated read-only model context for workspace relationships."""

from __future__ import annotations

import json
from typing import Any, Dict

from fastapi import Request
from jvspatial.api import endpoint
from pydantic import ValidationError

from app.api.errors import BadRequestError, MissingAuthenticationError
from app.api.utils import resolve_principal_id
from app.schemas.graph_context import GraphContextRequest
from app.services.graph_context import capture_workspace_graph
from app.services.request_scope import resolve_workspace_id_from_request


@endpoint("/graph-context", methods=["POST"], auth=True, tags=["Query"])
async def get_graph_context(request: Request) -> Dict[str, Any]:
    """Capture a bounded graph under the caller's live workspace and policy."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    workspace_id = await resolve_workspace_id_from_request(request, user_id)
    try:
        spec = GraphContextRequest.model_validate(await request.json())
    except (json.JSONDecodeError, UnicodeDecodeError, ValidationError) as exc:
        raise BadRequestError(message="Invalid graph context request") from exc
    try:
        return await capture_workspace_graph(
            principal_id=user_id, workspace_id=workspace_id or "", spec=spec
        )
    except ImportError as exc:
        raise BadRequestError(
            message="Graph context requires the qualified JV Spatial dev dependency"
        ) from exc
