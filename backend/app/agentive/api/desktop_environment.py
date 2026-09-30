"""Integral Desktop capability-host registration and WebSocket transport."""

import json
from typing import Any, Dict

from fastapi import (  # deviation: @endpoint does not support WebSocket routes
    APIRouter,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from jvspatial.api import endpoint
from pydantic import ValidationError

from app.agentive.services import desktop_environment
from app.api.errors import (
    BadRequestError,
    ResourceNotFoundError,
    ServiceUnavailableError,
)
from app.api.utils import resolve_principal_id
from app.schemas.agentive.desktop_environment import (
    DesktopEnvironmentSessionRequest,
    DesktopEnvironmentSessionResponse,
    DesktopEnvironmentStatusResponse,
)
from app.services.change_event import emit_change_event
from app.services.request_scope import resolve_workspace_id_from_request

router = APIRouter()
_MAX_HOST_MESSAGE_BYTES = 1024 * 1024


@endpoint(
    "/agentive/desktop-environments/session",
    methods=["POST"],
    auth=True,
    tags=["Agentive"],
)
async def create_desktop_environment_session(request: Request) -> Dict[str, Any]:
    """Mint a scoped, single-use ticket for Electron main."""
    user_id = resolve_principal_id(request)
    workspace_id = await resolve_workspace_id_from_request(request, user_id)
    try:
        body = DesktopEnvironmentSessionRequest.model_validate(await request.json())
        result = await desktop_environment.mint_session(
            principal_id=user_id,
            workspace_id=workspace_id,
            device_id=body.device_id,
            device_name=body.device_name,
        )
    except (ValueError, ValidationError) as exc:
        raise BadRequestError(message="Invalid desktop environment request") from exc
    except desktop_environment.DesktopEnvironmentError as exc:
        raise ServiceUnavailableError(message=exc.message) from exc
    connector_id = str(result.get("connector_id") or "")
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="connector.update",
        resource_type="Connector",
        resource_id=connector_id,
        before=None,
        after={
            "subclass_slug": "integral_desktop",
            "health_status": "unknown",
        },
        scope=f"connector:{connector_id}",
        details={"via": "desktop.environment.session"},
    )
    return DesktopEnvironmentSessionResponse.model_validate(result).model_dump()


@endpoint(
    "/agentive/desktop-environments/status",
    methods=["GET"],
    auth=True,
    tags=["Agentive"],
)
async def get_desktop_environment_status(request: Request) -> Dict[str, Any]:
    """Diagnose the current turn selector against the live host registry."""
    user_id = resolve_principal_id(request)
    workspace_id = await resolve_workspace_id_from_request(request, user_id)
    status = desktop_environment.binding_status(
        str(request.headers.get("X-Integral-Desktop-Environment") or "").strip(),
        principal_id=user_id,
        workspace_id=workspace_id,
    )
    return DesktopEnvironmentStatusResponse.model_validate(status).model_dump()


@endpoint(
    "/agentive/desktop-environments/artifacts/{artifact_id}",
    methods=["GET"],
    auth=True,
    tags=["Agentive"],
)
async def get_desktop_environment_artifact(
    request: Request, artifact_id: str
) -> Response:
    """Return one short-lived screenshot to its exact principal/workspace."""
    user_id = resolve_principal_id(request)
    workspace_id = await resolve_workspace_id_from_request(request, user_id)
    artifact = desktop_environment.read_artifact(
        artifact_id, principal_id=user_id, workspace_id=workspace_id
    )
    if artifact is None:
        raise ResourceNotFoundError(message="Desktop screenshot artifact not found")
    return Response(
        content=artifact.data,
        media_type=artifact.content_type,
        headers={
            "Cache-Control": "no-store, private",
            "Content-Security-Policy": "default-src 'none'",
            "X-Content-Type-Options": "nosniff",
            "ETag": f'"{artifact.sha256}"',
        },
    )


@router.websocket(desktop_environment.DESKTOP_WS_PATH)
async def desktop_environment_websocket(websocket: WebSocket) -> None:
    """Carry bounded capability calls to one authenticated desktop host."""
    await websocket.accept()
    ticket = desktop_environment.consume_ticket(
        str(websocket.query_params.get("ticket") or "")
    )
    if ticket is None:
        await websocket.close(code=4001, reason="Invalid or expired desktop ticket")
        return
    connection = await desktop_environment.register_connection(ticket, websocket)
    try:
        await websocket.send_text(
            json.dumps(
                {
                    "type": "ready",
                    "binding_id": connection.binding_id,
                    "manifest_version": "1",
                }
            )
        )
        while True:
            event = await websocket.receive()
            if event.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect()
            binary = event.get("bytes")
            if binary is not None:
                try:
                    desktop_environment.accept_artifact_frame(connection, binary)
                except desktop_environment.DesktopEnvironmentError:
                    await websocket.close(
                        code=4010, reason="Invalid desktop artifact frame"
                    )
                    return
                continue
            raw = event.get("text") or ""
            if len(raw.encode("utf-8")) > _MAX_HOST_MESSAGE_BYTES:
                await websocket.close(
                    code=4009, reason="Desktop host message exceeds size limit"
                )
                return
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if message.get("type") in {"result", "driver_result"}:
                desktop_environment.accept_result(connection, message)
            elif message.get("type") == "host_manifest":
                desktop_environment.accept_host_manifest(connection, message)
            elif message.get("type") == "ping":
                await websocket.send_text('{"type":"pong"}')
    except WebSocketDisconnect:
        pass
    finally:
        await desktop_environment.unregister_connection(connection)
