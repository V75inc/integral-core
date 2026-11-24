"""Management API for admitted drop-in connector packages."""

from fastapi import Request
from jvspatial.api import endpoint

from app.agentive.services.connector_packages import (
    approve_review,
    attach_host_grants,
    catalogue,
    configure_package,
    install_package,
    installations,
    invoke_action,
    invoke_mcp_tool,
    projection,
    receive_package_webhook,
    refresh_package_action_tools,
    remove_package,
    rollback_package,
    scan_now,
    send_package_message,
    set_enabled,
    synchronize_installation,
    upgrade_package,
)
from app.agentive.services.profile_management import require_profile_manager
from app.api.errors import BadRequestError, MissingAuthenticationError
from app.api.utils import resolve_principal_id
from app.schemas.agentive.connector_packages import (
    ConnectorPackageAction,
    ConnectorPackageEnablement,
    ConnectorPackageValues,
)
from app.services.permissions import get_user_node
from app.services.scope_header import parse_scope_header

_PATH = "/agentive/connector-packages"


def _workspace(request: Request, explicit: str | None = None) -> str:
    workspace_id = explicit or request.query_params.get("workspace_id")
    if not workspace_id:
        raise BadRequestError("workspace_id is required")
    return workspace_id


async def _admin(request: Request, workspace_id: str) -> str:
    actor = await get_user_node(resolve_principal_id(request))
    if actor is None:
        raise MissingAuthenticationError("Authentication required")
    selected = parse_scope_header(request.headers.get("X-Integral-Scope"))
    if selected is None or selected.get("workspace_id") != workspace_id:
        raise BadRequestError("An exact workspace scope is required")
    await require_profile_manager(actor.id, workspace_id)
    return actor.id


@endpoint(_PATH, methods=["GET"], auth=True, tags=["Agentive"])
async def list_connector_packages(request: Request) -> dict:
    """Workspace packages and the reasons rejected packages were not offered."""
    workspace_id = _workspace(request)
    actor = await _admin(request, workspace_id)
    listed = catalogue()
    await attach_host_grants(listed["packages"], workspace_id)
    listed["installations"] = await installations(workspace_id, actor)
    return listed


@endpoint(f"{_PATH}/rescan", methods=["POST"], auth=True, tags=["Agentive"])
async def rescan_connector_packages(request: Request) -> dict:
    """Re-read connector roots. Declarative packages do not need a restart."""
    workspace_id = _workspace(request)
    actor = await _admin(request, workspace_id)
    scan_now()
    await refresh_package_action_tools()
    listed = catalogue()
    await attach_host_grants(listed["packages"], workspace_id)
    listed["installations"] = await installations(workspace_id, actor)
    return listed


@endpoint(
    f"{_PATH}/{{package_id}}/install", methods=["POST"], auth=True, tags=["Agentive"]
)
async def install_connector_package(request: Request, package_id: str) -> dict:
    """Add a disabled installation."""
    body = ConnectorPackageValues.model_validate(await request.json())
    actor = await _admin(request, _workspace(request, body.workspace_id))
    connector = await install_package(
        actor_id=actor,
        workspace_id=body.workspace_id,
        package_id=package_id,
        values=body.values,
    )
    return projection(connector)


@endpoint(
    f"{_PATH}/installations/{{connector_id}}",
    methods=["PUT"],
    auth=True,
    tags=["Agentive"],
)
async def configure_connector_package(request: Request, connector_id: str) -> dict:
    """Replace configuration. Secrets are stored encrypted and the install is disabled."""
    body = ConnectorPackageValues.model_validate(await request.json())
    workspace_id = _workspace(request, body.workspace_id)
    actor = await _admin(request, workspace_id)
    connector = await configure_package(
        connector_id, actor, body.values, workspace_id=workspace_id
    )
    return projection(connector)


@endpoint(
    f"{_PATH}/installations/{{connector_id}}/upgrade",
    methods=["POST"],
    auth=True,
    tags=["Agentive"],
)
async def upgrade_connector_package(request: Request, connector_id: str) -> dict:
    """Move the installation onto the admitted manifest and keep one rollback."""
    workspace_id = _workspace(request)
    actor = await _admin(request, workspace_id)
    connector = await upgrade_package(connector_id, actor, workspace_id=workspace_id)
    return projection(connector)


@endpoint(
    f"{_PATH}/installations/{{connector_id}}/rollback",
    methods=["POST"],
    auth=True,
    tags=["Agentive"],
)
async def rollback_connector_package(request: Request, connector_id: str) -> dict:
    """Restore the installation saved by the last upgrade."""
    workspace_id = _workspace(request)
    actor = await _admin(request, workspace_id)
    connector = await rollback_package(connector_id, actor, workspace_id=workspace_id)
    return projection(connector)


@endpoint(
    f"{_PATH}/installations/{{connector_id}}/enabled",
    methods=["PUT"],
    auth=True,
    tags=["Agentive"],
)
async def enable_connector_package(request: Request, connector_id: str) -> dict:
    """Enable only after the manifest health probe passes."""
    body = ConnectorPackageEnablement.model_validate(await request.json())
    workspace_id = _workspace(request)
    actor = await _admin(request, workspace_id)
    connector = await set_enabled(
        connector_id, actor, body.enabled, workspace_id=workspace_id
    )
    return projection(connector)


@endpoint(
    f"{_PATH}/installations/{{connector_id}}",
    methods=["DELETE"],
    auth=True,
    tags=["Agentive"],
)
async def delete_connector_package(request: Request, connector_id: str) -> dict:
    """Disable, then delete the installation."""
    workspace_id = _workspace(request)
    actor = await _admin(request, workspace_id)
    await remove_package(connector_id, actor, workspace_id=workspace_id)
    return {"removed": connector_id}


@endpoint(
    f"{_PATH}/installations/{{connector_id}}/sync",
    methods=["POST"],
    auth=True,
    tags=["Agentive"],
)
async def sync_connector_package(request: Request, connector_id: str) -> dict:
    """Synchronize the installation's authorized Track binding."""
    workspace_id = _workspace(request)
    actor = await _admin(request, workspace_id)
    stats = await synchronize_installation(connector_id, actor, workspace_id)
    return {"connector_id": connector_id, "stats": stats}


@endpoint(
    f"{_PATH}/installations/{{connector_id}}/webhook",
    methods=["POST"],
    auth=False,
    tags=["Agentive"],
)
async def connector_package_webhook(request: Request, connector_id: str) -> dict:
    """Accept a signed provider webhook. The body is ignored until the signature matches."""
    raw = await request.body()
    if len(raw) > 65536:
        raise BadRequestError("Webhook body is too large")
    return await receive_package_webhook(connector_id, raw, request.headers)


@endpoint(
    f"{_PATH}/installations/{{connector_id}}/messages",
    methods=["POST"],
    auth=True,
    tags=["Agentive"],
)
async def send_connector_package_message(request: Request, connector_id: str) -> dict:
    """Send one message through the package's declared webhook request."""
    body = ConnectorPackageAction.model_validate(await request.json())
    workspace_id = _workspace(request)
    actor = await _admin(request, workspace_id)
    return await send_package_message(
        connector_id, actor, body.input, workspace_id=workspace_id
    )


@endpoint(
    f"{_PATH}/installations/{{connector_id}}/mcp/{{tool_name}}",
    methods=["POST"],
    auth=True,
    tags=["Agentive"],
)
async def call_connector_mcp_tool(
    request: Request, connector_id: str, tool_name: str
) -> dict:
    """Run a read-only MCP tool, or stage any tool the manifest did not certify."""
    body = ConnectorPackageAction.model_validate(await request.json())
    workspace_id = _workspace(request)
    actor = await _admin(request, workspace_id)
    return await invoke_mcp_tool(
        connector_id, actor, tool_name, body.input, workspace_id=workspace_id
    )


@endpoint(
    f"{_PATH}/installations/{{connector_id}}/actions/{{action_name}}",
    methods=["POST"],
    auth=True,
    tags=["Agentive"],
)
async def call_connector_action(
    request: Request, connector_id: str, action_name: str
) -> dict:
    """Run a read action, or stage a write for review."""
    body = ConnectorPackageAction.model_validate(await request.json())
    workspace_id = _workspace(request)
    actor = await _admin(request, workspace_id)
    return await invoke_action(
        connector_id, actor, action_name, body.input, workspace_id=workspace_id
    )


@endpoint(
    f"{_PATH}/installations/{{connector_id}}/reviews/{{review_id}}",
    methods=["POST"],
    auth=True,
    tags=["Agentive"],
)
async def approve_connector_review(
    request: Request, connector_id: str, review_id: str
) -> dict:
    """Run a staged write after the same admin approves it."""
    workspace_id = _workspace(request)
    actor = await _admin(request, workspace_id)
    return await approve_review(
        connector_id, actor, review_id, workspace_id=workspace_id
    )
