"""Shared App/Track archive transport; all graph work stays in services."""

from fastapi import Request
from fastapi.responses import Response
from jvspatial.api import endpoint
from pydantic import ValidationError

from app.api.errors import BadRequestError, MissingAuthenticationError
from app.api.utils import resolve_principal_id
from app.schemas.portable_transfer import ExportSelection
from app.services.portable_archive import MAX_ARCHIVE_BYTES
from app.services.portable_transfer import (
    archive_summary,
    export_resources,
    import_resources,
    read_archive,
)
from app.services.request_scope import resolve_workspace_id_from_request


async def _scope(request: Request) -> tuple[str, str]:
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    from app.services.permissions import get_user_node

    profile = await get_user_node(user_id)
    if profile is None:
        raise MissingAuthenticationError(message="Authenticated profile is unavailable")
    return profile.id, await resolve_workspace_id_from_request(request, user_id)


async def _archive_body(request: Request) -> bytes:
    chunks = bytearray()
    async for chunk in request.stream():
        if len(chunks) + len(chunk) > MAX_ARCHIVE_BYTES:
            raise BadRequestError(message="Portable archive exceeds 50 MiB")
        chunks.extend(chunk)
    return bytes(chunks)


@endpoint("/portable/export", methods=["POST"], auth=True, tags=["Portability"])
async def export_portable_resources(request: Request) -> Response:
    """Download a selection using the shared permission-scoped archive service."""
    user_id, workspace_id = await _scope(request)
    try:
        body = ExportSelection.model_validate(await request.json())
    except (ValidationError, ValueError) as exc:
        raise BadRequestError(
            message="Select Apps or Tracks and a valid export option"
        ) from exc
    content = await export_resources(
        user_id=user_id, workspace_id=workspace_id, selection=body
    )
    return Response(
        content=content,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="integral-{body.kind}s-{body.mode}.integral.zip"',
            "Cache-Control": "no-store",
        },
    )


@endpoint("/portable/preview", methods=["POST"], auth=True, tags=["Portability"])
async def preview_portable_resources(request: Request) -> dict:
    """Validate an archive without creating resources."""
    await _scope(request)
    archive, _ = await read_archive(await _archive_body(request))
    return archive_summary(archive)


@endpoint("/portable/import", methods=["POST"], auth=True, tags=["Portability"])
async def import_portable_resources(request: Request) -> dict:
    """Import new private copies in the currently authenticated workspace."""
    user_id, workspace_id = await _scope(request)
    return await import_resources(
        user_id=user_id,
        workspace_id=workspace_id,
        data=await _archive_body(request),
        app_id=request.query_params.get("app_id", ""),
    )
