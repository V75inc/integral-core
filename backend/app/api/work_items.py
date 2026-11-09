"""Authenticated observation surface for durable work."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import Request
from jvspatial.api import endpoint

from app.agentive.services.work_visibility import (
    get_visible_work_item,
    list_visible_work_items,
)
from app.api.errors import BadRequestError, MissingAuthenticationError
from app.api.utils import resolve_principal_id
from app.schemas.agentive.steering import SteeringCorrectionRequest


@endpoint("/work-items", methods=["GET"], auth=True, tags=["Work"])
async def get_work_items(
    request: Request, workspace_id: str = "", limit: int = 50, cursor: str = ""
) -> Dict[str, Any]:
    """List only work state authorized within the requested workspace."""
    principal_id = resolve_principal_id(request)
    if not principal_id:
        raise MissingAuthenticationError(message="Authentication required")
    rows, next_cursor = await list_visible_work_items(
        workspace_id=workspace_id, principal_id=principal_id, limit=limit, cursor=cursor
    )
    return {
        "items": [row.model_dump(exclude_none=True) for row in rows],
        "next_cursor": next_cursor,
    }


@endpoint("/work-items/{work_item_id}", methods=["GET"], auth=True, tags=["Work"])
async def get_work_item(request: Request, work_item_id: str) -> Dict[str, Any]:
    """Return safe lifecycle state for a work item the caller may observe."""
    principal_id = resolve_principal_id(request)
    if not principal_id:
        raise MissingAuthenticationError(message="Authentication required")
    result = await get_visible_work_item(
        work_item_id=work_item_id,
        principal_id=principal_id,
    )
    return result.model_dump(exclude_none=True)


@endpoint(
    "/work-items/{work_item_id}/steering", methods=["POST"], auth=True, tags=["Work"]
)
async def post_work_item_steering(
    request: Request, work_item_id: str
) -> dict[str, str]:
    """Accept a correction for the caller's active interactive chat turn."""
    principal_id = resolve_principal_id(request)
    if not principal_id:
        raise MissingAuthenticationError(message="Authentication required")
    from app.services.chat_steering import accept_steering_correction

    try:
        correction = SteeringCorrectionRequest.model_validate(await request.json())
    except ValueError:
        raise BadRequestError(
            message="A correction and client request ID are required"
        ) from None
    return await accept_steering_correction(work_item_id, principal_id, correction)
