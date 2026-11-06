"""Authenticated compact picker reads. Public-share policy remains separate."""

from fastapi import Request
from jvspatial.api import endpoint
from pydantic import ValidationError

from app.api.errors import BadRequestError, MissingAuthenticationError
from app.api.utils import resolve_principal_id
from app.schemas.relation_options import RelationOptionsRequest
from app.services.perf_trace import perf_span
from app.services.relation_options import relation_options
from app.services.request_scope import resolve_workspace_id_from_request


@endpoint("/relation-options", methods=["GET"], auth=True, tags=["Entries"])
async def get_relation_options(
    request: Request,
    track_id: str,
    entry_type_id: str,
    field_key: str,
    entry_id: str | None = None,
    q: str = "",
    cursor: str | None = None,
    limit: int = 50,
) -> dict:
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    workspace_id = await resolve_workspace_id_from_request(request, user_id)
    try:
        params = RelationOptionsRequest(
            track_id=track_id,
            entry_type_id=entry_type_id,
            field_key=field_key,
            entry_id=entry_id,
            q=q,
            cursor=cursor,
            limit=limit,
        )
    except ValidationError:
        raise BadRequestError(message="Invalid relation options request") from None
    response = await relation_options(user_id, workspace_id, params)
    async with perf_span("relations.serialize"):
        return response.model_dump()
