"""Authenticated observation surface for durable work."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import Request
from jvspatial.api import endpoint

from app.agentive.services.work_visibility import get_visible_work_item
from app.api.errors import MissingAuthenticationError
from app.api.utils import resolve_principal_id


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
