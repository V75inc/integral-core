"""Readiness and declarative index management under current Track authority."""

from fastapi import Request
from jvspatial.api import endpoint
from pydantic import ValidationError

from app.api.errors import BadRequestError, MissingAuthenticationError
from app.api.utils import resolve_principal_id
from app.services.request_scope import resolve_workspace_id_from_request
from app.services.retrieval import index_management


async def _scope(request: Request):
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    return user_id, await resolve_workspace_id_from_request(request, user_id)


@endpoint(
    "/tracks/{track_id}/search-index", methods=["GET"], auth=True, tags=["Retrieve"]
)
async def get_search_index(request: Request, track_id: str) -> dict:
    """Read bounded, current-profile readiness and coverage."""
    user, workspace = await _scope(request)
    return await index_management.index_status(user, track_id, workspace)


@endpoint(
    "/tracks/{track_id}/search-index", methods=["PUT"], auth=True, tags=["Retrieve"]
)
async def configure_search_index(request: Request, track_id: str) -> dict:
    """Save an authorized declaration and queue its rebuild."""
    user, workspace = await _scope(request)
    try:
        return await index_management.configure_index(
            user, track_id, await request.json(), workspace
        )
    except (ValidationError, ValueError) as exc:
        raise BadRequestError(
            message="Choose valid search fields and settings"
        ) from exc


@endpoint(
    "/tracks/{track_id}/search-index/reindex",
    methods=["POST"],
    auth=True,
    tags=["Retrieve"],
)
async def reindex_track(request: Request, track_id: str) -> dict:
    """Queue a rebuild under current Track authority."""
    user, workspace = await _scope(request)
    return await index_management.request_reindex(user, track_id, workspace)
