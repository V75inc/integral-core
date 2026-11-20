"""Per-user saved signature vault with an optional trusted host provider."""

from __future__ import annotations

from typing import Any, Dict, Optional

from jvspatial.api import endpoint
from starlette.requests import Request
from starlette.responses import Response

from app.api.errors import BadRequestError, ResourceNotFoundError
from app.api.utils import resolve_principal_id
from app.schemas.user_signatures import UserSignatureSaveRequest
from app.services.host_hooks import invoke_signature_vault_provider
from app.services.request_scope import resolve_workspace_id_from_request
from app.services.user_signatures import (
    decode_signature_upload,
    delete_user_signature,
    list_user_signatures,
    read_user_signature_png,
    save_user_signature,
)


async def _workspace_for_vault(request: Request, user_id: str) -> Optional[str]:
    # An unauthorized or invalid explicit scope is never a personal fallback.
    raw = request.headers.get("x-integral-scope")
    if raw is not None and not str(raw).strip():
        raise BadRequestError(message="Invalid X-Integral-Scope header")
    return await resolve_workspace_id_from_request(request, user_id)


@endpoint("/me/signatures", methods=["GET"], auth=True, tags=["Users"])
async def api_list_my_signatures(request: Request) -> Dict[str, Any]:
    user_id = resolve_principal_id(request)
    if not user_id:
        raise BadRequestError(message="Authentication required")
    ws_id = await _workspace_for_vault(request, user_id)
    if ws_id:
        dispatched = await invoke_signature_vault_provider("list", ws_id, user_id, {})
        if isinstance(dispatched, list):
            return {"signatures": dispatched, "total": len(dispatched)}
    items = await list_user_signatures(user_id)
    return {"signatures": items, "total": len(items)}


@endpoint("/me/signatures", methods=["POST"], auth=True, tags=["Users"])
async def api_save_my_signature(request: Request) -> Dict[str, Any]:
    user_id = resolve_principal_id(request)
    if not user_id:
        raise BadRequestError(message="Authentication required")
    body = UserSignatureSaveRequest.model_validate(await request.json())
    ws_id = await _workspace_for_vault(request, user_id)
    if ws_id:
        saved = await invoke_signature_vault_provider(
            "save",
            ws_id,
            user_id,
            {"signature_png": body.signature_png, "label": body.label or "Default"},
        )
        if isinstance(saved, dict):
            return saved
    try:
        png = decode_signature_upload(body.signature_png)
    except ValueError as exc:
        raise BadRequestError(message=str(exc)) from exc
    return await save_user_signature(
        user_id, png_bytes=png, label=body.label or "Default"
    )


@endpoint(
    "/me/signatures/{attachment_id}", methods=["DELETE"], auth=True, tags=["Users"]
)
async def api_delete_my_signature(
    request: Request, attachment_id: str
) -> Dict[str, Any]:
    user_id = resolve_principal_id(request)
    if not user_id:
        raise BadRequestError(message="Authentication required")
    ws_id = await _workspace_for_vault(request, user_id)
    if ws_id:
        deleted = await invoke_signature_vault_provider(
            "delete", ws_id, user_id, {"attachment_id": attachment_id}
        )
        if deleted is not None:
            if not deleted:
                raise ResourceNotFoundError(message="Signature not found")
            return {"deleted": True, "id": attachment_id}
    try:
        ok = await delete_user_signature(user_id, attachment_id)
    except PermissionError as exc:
        raise BadRequestError(message=str(exc)) from exc
    if not ok:
        raise ResourceNotFoundError(message="Signature not found")
    return {"deleted": True, "id": attachment_id}


@endpoint(
    "/me/signatures/{attachment_id}/download",
    methods=["GET"],
    auth=True,
    tags=["Users"],
)
async def api_download_my_signature(request: Request, attachment_id: str) -> Response:
    user_id = resolve_principal_id(request)
    if not user_id:
        raise BadRequestError(message="Authentication required")
    if request.headers.get("x-integral-scope") is not None:
        await _workspace_for_vault(request, user_id)
    items = await list_user_signatures(user_id)
    if not any(i.get("id") == attachment_id for i in items):
        raise ResourceNotFoundError(message="Signature not found")
    png = await read_user_signature_png(user_id)
    if not png:
        raise ResourceNotFoundError(message="Signature not found")
    return Response(content=png, media_type="image/png")
