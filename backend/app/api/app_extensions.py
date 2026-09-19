"""App extension surface — typed operations and view host (ADR-011)."""

from __future__ import annotations

import uuid
from typing import Any, Dict

from fastapi import Request
from fastapi.responses import FileResponse
from jvspatial.api import endpoint

from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    MissingAuthenticationError,
    ResourceNotFoundError,
)
from app.api.utils import resolve_principal_id
from app.schemas.app_extension_views import (
    ExtensionViewHandshakeResponse,
    ExtensionViewsListResponse,
)
from app.schemas.app_operations import (
    AppOperationInvokeRequest,
    AppOperationInvokeResponse,
    AppOperationsListResponse,
)
from app.services.app_extension_views import (
    list_extension_views,
    serve_extension_view_asset,
    theme_tokens_for_host,
)
from app.services.request_scope import resolve_workspace_id_from_request


@endpoint(
    "/extensions/{app_id}/operations",
    methods=["GET"],
    auth=True,
    tags=["App Extensions"],
)
async def list_operations(request: Request, app_id: str) -> Dict[str, Any]:
    """List typed operations registered for an app instance."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    workspace_id = await resolve_workspace_id_from_request(request, user_id)
    from app.services.app_operations.dispatch import list_app_operations

    result = await list_app_operations(
        user_id=user_id,
        workspace_id=workspace_id or "",
        app_id=app_id,
    )
    from app.agentive.services.execution_runs import build_capability_snapshot

    snapshot = await build_capability_snapshot(workspace_id or "")
    app_snapshot: Dict[str, Any] = next(
        (
            item
            for item in snapshot.get("apps") or []
            if isinstance(item, dict) and str(item.get("app_id") or "") == app_id
        ),
        {},
    )
    result["queries"] = list(app_snapshot.get("queries") or [])
    return AppOperationsListResponse.model_validate(result).model_dump()


@endpoint(
    "/extensions/{app_id}/operations/{operation_key}",
    methods=["POST"],
    auth=True,
    tags=["App Extensions"],
)
async def invoke_operation(
    request: Request, app_id: str, operation_key: str
) -> Dict[str, Any]:
    """Invoke a typed app operation through the Core dispatcher."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    workspace_id = await resolve_workspace_id_from_request(request, user_id)
    raw = await request.json() if request.method == "POST" else {}
    body = AppOperationInvokeRequest.model_validate(raw or {})
    idempotency_key = request.headers.get("Idempotency-Key") or request.headers.get(
        "idempotency-key"
    )
    from app.agentive.services.capability_broker import invoke_declared_capability
    from app.agentive.services.execution_runs import build_capability_snapshot

    snapshot = await build_capability_snapshot(workspace_id or "")
    app_snapshot: Dict[str, Any] = next(
        (
            item
            for item in snapshot.get("apps") or []
            if isinstance(item, dict) and str(item.get("app_id") or "") == app_id
        ),
        {},
    )
    is_query = any(
        isinstance(query, dict) and str(query.get("key") or "") == operation_key
        for query in app_snapshot.get("queries") or []
    )

    origin_header = (
        (
            request.headers.get("X-Integral-Run-Origin")
            or request.headers.get("x-integral-run-origin")
            or ""
        )
        .strip()
        .lower()
    )
    origin = "view" if origin_header == "view" else "http"
    result = await invoke_declared_capability(
        principal_id=user_id,
        workspace_id=workspace_id or "",
        capability_key=operation_key,
        origin=origin,
        source="app",
        op_class="read" if is_query else "execute",
        arguments=body.input,
        app_id=app_id,
        idempotency_key=idempotency_key,
    )
    if not result.ok:
        if result.error_code in {
            "capability.identity_mismatch",
            "capability.workspace_mismatch",
            "capability.revoked",
            "capability.denied",
        }:
            raise InsufficientPermissionsError(message=result.message)
        if result.error_code in {"capability.not_in_snapshot"}:
            raise ResourceNotFoundError(message=result.message)
        raise BadRequestError(
            message=result.message,
            details={"error_code": result.error_code},
        )
    output = result.data if isinstance(result.data, dict) else {"result": result.data}
    if "output" not in output and "app_id" not in output:
        output = {
            "app_id": app_id,
            "operation_key": operation_key,
            "output": output,
        }
    payload = {
        "app_id": output.get("app_id") or app_id,
        "operation_key": output.get("operation_key") or operation_key,
        "output": output.get("output") if "output" in output else output,
        "receipt": result.receipt.model_dump() if result.receipt else None,
    }
    return AppOperationInvokeResponse.model_validate(payload).model_dump()


@endpoint(
    "/extensions/{app_id}/views",
    methods=["GET"],
    auth=True,
    tags=["App Extensions"],
)
async def list_extension_views_route(request: Request, app_id: str) -> Dict[str, Any]:
    """List package-owned extension views for an app instance."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    workspace_id = await resolve_workspace_id_from_request(request, user_id)
    mount_id = request.headers.get("X-Extension-Mount-Id") or str(uuid.uuid4())
    view_key = request.query_params.get("view_key")
    result = await list_extension_views(
        user_id=user_id,
        workspace_id=workspace_id or "",
        app_id=app_id,
        mount_id=mount_id,
        view_key=view_key,
    )
    return ExtensionViewsListResponse.model_validate(result).model_dump()


@endpoint(
    "/extensions/{app_id}/views/{view_key}/handshake",
    methods=["GET"],
    auth=True,
    tags=["App Extensions"],
)
async def extension_view_handshake_route(
    request: Request, app_id: str, view_key: str
) -> Dict[str, Any]:
    """Mint a per-mount handshake token for the iframe view bridge."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    workspace_id = await resolve_workspace_id_from_request(request, user_id)
    mount_id = request.headers.get("X-Extension-Mount-Id") or str(uuid.uuid4())
    result = await list_extension_views(
        user_id=user_id,
        workspace_id=workspace_id or "",
        app_id=app_id,
        mount_id=mount_id,
        view_key=view_key,
    )
    payload = ExtensionViewHandshakeResponse(
        app_id=app_id,
        view_key=view_key,
        package_version=result.get("package_version"),
        handshake_token=result["handshake_token"],
        protocol=result["protocol"],
        theme=theme_tokens_for_host(),
    )
    return payload.model_dump()


@endpoint(
    "/extensions/{app_id}/views/{view_key}/{asset_path:path}",
    methods=["GET"],
    auth=True,
    tags=["App Extensions"],
)
async def serve_extension_view_asset_route(
    request: Request,
    app_id: str,
    view_key: str,
    asset_path: str,
):
    """Serve a verified static asset from a package extension view."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    workspace_id = await resolve_workspace_id_from_request(request, user_id)
    file_path, media_type = await serve_extension_view_asset(
        user_id=user_id,
        workspace_id=workspace_id or "",
        app_id=app_id,
        view_key=view_key,
        asset_path=asset_path,
    )
    return FileResponse(
        path=str(file_path),
        media_type=media_type,
        headers={"Cache-Control": "private, max-age=60"},
    )
