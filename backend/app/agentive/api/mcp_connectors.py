"""MCP OAuth callback REST surface (I-CON-06).

``POST /api/agentive/connectors/mcp/oauth/callback`` — exchange the
authorization code from the popup, persist tokens, discover tools.

Mount / refresh live in ``connectors.py`` (stdio + HTTP). Catalog HTTP
installs go through ``mcp_adapter`` and finish here after the IdP redirect.
"""

from __future__ import annotations

from fastapi import Request
from jvspatial.api import endpoint

from app.agentive.api.connectors import _audit_snapshot, _to_response
from app.api.errors import (
    BadRequestError,
    ConnectorAuthError,
    InsufficientPermissionsError,
    MissingAuthenticationError,
    ResourceNotFoundError,
    ServiceUnavailableError,
)
from app.api.utils import resolve_principal_id
from app.schemas.agentive.connectors import (
    ConnectorResponse,
    McpOAuthCallbackRequest,
)
from app.schemas.policy import Resource, Subject
from app.services.change_event import emit_change_event
from app.services.policy_engine import evaluate as policy_evaluate


@endpoint(
    "/agentive/connectors/mcp/oauth/callback",
    methods=["POST"],
    auth=True,
    tags=["Agentive"],
)
async def post_mcp_oauth_callback(
    request: Request,
) -> ConnectorResponse:
    """Finish MCP OAuth: exchange the code, discover tools, register them."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    try:
        raw_body = await request.json()
    except Exception:  # noqa: BLE001
        raw_body = {}
    if not isinstance(raw_body, dict):
        raise BadRequestError(message="Request body must be a JSON object")
    try:
        body = McpOAuthCallbackRequest.model_validate(raw_body)
    except Exception as e:  # noqa: BLE001
        raise BadRequestError(
            message="Invalid OAuth callback body",
            details={"validation": str(e)},
        )

    decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="connector.update",
        resource=Resource(
            kind="connector",
            id="",
            scope=f"user:{user_id}",
        ),
    )
    if not decision.allowed:
        raise InsufficientPermissionsError(
            message="connector.update denied",
            details={"decision_reason": decision.reason},
        )

    from app.agentive.connectors.mcp_adapter import complete_mcp_oauth

    try:
        connector = await complete_mcp_oauth(
            user_id=user_id, code=body.code, state=body.state
        )
    except (
        BadRequestError,
        ConnectorAuthError,
        ResourceNotFoundError,
        InsufficientPermissionsError,
    ):
        raise
    except Exception as e:  # noqa: BLE001
        raise ServiceUnavailableError(
            message="Failed to discover tools after MCP OAuth",
            details={"reason": str(type(e).__name__)},
        ) from e

    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="connector.update",
        resource_type="Connector",
        resource_id=connector.id,
        before=None,
        after=_audit_snapshot(connector),
        scope=f"connector:{connector.id}",
        details={"via": "mcp.oauth.callback", "subclass_slug": "mcp"},
    )
    return _to_response(connector)
