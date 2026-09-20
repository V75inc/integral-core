"""Agent uplink registration and heartbeat endpoints."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import Request
from jvspatial.api import endpoint

from app.agentive.middleware.service_auth import verify_service_key
from app.agentive.nodes import AgentConfig
from app.agentive.services.uplink_registry import uplink_registry
from app.agentive.types import AgentType
from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    MissingAuthenticationError,
)
from app.api.utils import export_node, resolve_principal_id
from app.schemas.agentive.uplink import RegisterAgentResponse
from app.schemas.audit import ChangeEventAction
from app.services.change_event import emit_change_event
from app.services.workspace_permissions import is_workspace_admin_or_owner

# Facets a user JWT may register through ``/uplink/register``. The ``system``
# facet is the deployment-wide singleton and is registered ONLY via the
# service-key route ``/uplink/register-system``.
_USER_REGISTRABLE_SCOPES = ("personal", "org_facing")


def _require_service_key(request: Request) -> None:
    """Validate the X-Integral-Service-Key header. Raises 401 on failure.

    Pre-migration this raised ``HTTPException(401, …)``; the 06-05 rewrite
    routes through ``MissingAuthenticationError`` so the envelope is the
    canonical 5-key shape (per AGENTS.md § Forbidden Patterns).
    """
    sk = request.headers.get("x-integral-service-key", "")
    if not verify_service_key(sk):
        raise MissingAuthenticationError(
            message="Invalid or missing X-Integral-Service-Key"
        )


@endpoint(
    "/agentive/uplink/register",
    methods=["POST"],
    auth=True,
    tags=["Agentive"],
)
async def register_agent(
    request: Request,
    scope: str = "personal",
    agent_type: AgentType = "jvagent",
    capabilities: Optional[List[str]] = None,
    uplink_url: str = "",
    persona: str = "",
    workspace_id: Optional[str] = None,
    preferences: Optional[Dict[str, Any]] = None,
) -> RegisterAgentResponse:
    """Register an agent with the Integral uplink.

    Creates or updates an AgentConfig and registers the agent
    in the live uplink registry.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    # A user JWT may only register its own personal facet, or an org-facing
    # facet for a workspace it owns / administers. ``scope="system"`` used to
    # be accepted here, letting any user overwrite the singleton system
    # AgentConfig (agent_type / uplink_url / persona) — a deployment-wide
    # chat outage from one request.
    if scope not in _USER_REGISTRABLE_SCOPES:
        raise BadRequestError(
            message="scope must be 'personal' or 'org_facing' "
            "(the system facet registers via /uplink/register-system)"
        )
    if scope == "org_facing":
        if not workspace_id:
            raise BadRequestError(message="workspace_id is required for org_facing")
        if not await is_workspace_admin_or_owner(user_id, workspace_id):
            raise InsufficientPermissionsError(
                message="Only a workspace owner or admin can register its agent"
            )

    capabilities = list(capabilities or [])
    _ = preferences  # accepted but unused at the current contract; future Phase

    # ``capabilities`` persists as inert descriptive metadata (ADR-003). The
    # former A2A catalogue-validation gate (I-A2A-01) was retired with the
    # agent-to-agent fabric.

    now = datetime.now(timezone.utc).isoformat()

    workspace_store = workspace_id or ""
    workspace_for_query = workspace_store if workspace_store else None

    # Find or create AgentConfig
    existing = await AgentConfig.find_one(
        user_id=user_id if scope == "personal" else "",
        scope=scope,
        workspace_id=workspace_for_query,
    )

    prior_snapshot = await export_node(existing) if existing else None  # D-03 before
    if existing:
        existing.agent_type = agent_type
        existing.capabilities = capabilities
        existing.uplink_url = uplink_url
        existing.persona = persona
        existing.is_active = True
        existing.last_connected_at = now
        existing.updated_at = now
        existing.scope = scope
        # Dual-write until ADR-003 facet collapse (Full Sweep F1).
        existing.facet = scope
        if scope == "personal":
            existing.user_id = user_id
        existing.workspace_id = workspace_store if workspace_store else None
        await existing.save()
        config = existing
        action: ChangeEventAction = "agent_config.update"
        try:
            from app.agentive.services.agent_registry_node import (
                wire_agent_config_attachment_edge,
            )

            await wire_agent_config_attachment_edge(config)
        except Exception:
            import logging as _logging

            _logging.getLogger(__name__).warning(
                "register_agent: wire_agent_config_attachment_edge failed for %s",
                config.id,
            )
    else:
        from app.agentive.services.agent_registry_node import (
            register_agent_config,
        )

        config = await register_agent_config(
            actor_id=user_id,
            user_id=user_id if scope == "personal" else "",
            scope=scope,
            facet=scope,
            agent_type=agent_type,
            capabilities=capabilities,
            uplink_url=uplink_url,
            persona=persona,
            is_active=True,
            workspace_id=workspace_store if workspace_store else None,
            last_connected_at=now,
        )
        action = "agent_config.register"

    # Register in live registry
    config_id = await uplink_registry.register(config)

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action=action,
        resource_type="AgentConfig",
        resource_id=config.id,
        before=prior_snapshot,
        after=await export_node(config),
        scope=f"user:{user_id}",
    )

    return RegisterAgentResponse(
        agent_config_id=config_id,
        status="registered",
        capabilities=capabilities,
    )


@endpoint(
    "/agentive/uplink/heartbeat",
    methods=["GET"],
    auth=True,
    tags=["Agentive"],
)
async def agent_heartbeat(request: Request) -> Dict[str, Any]:
    """Agent heartbeat — keeps the agent marked as connected.

    Should be called periodically (every 30-60 seconds).
    After 2 minutes without a heartbeat, the agent is marked disconnected.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    conn = await uplink_registry.get_agent_for_user(user_id)
    if conn:
        await uplink_registry.heartbeat(conn.config_id)
        return {"status": "alive", "agent_config_id": conn.config_id}

    return {"status": "not_registered"}


@endpoint(
    "/agentive/uplink/unregister",
    methods=["POST"],
    auth=True,
    tags=["Agentive"],
)
async def unregister_agent(request: Request) -> Dict[str, Any]:
    """Unregister an agent from the uplink."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    conn = await uplink_registry.get_agent_for_user(user_id)
    if conn:
        await uplink_registry.unregister(conn.config_id)

        # D-05 single emission path. Sync inline emit before HTTP response (D-06).
        # Persisted AgentConfig is not deleted — just unregistered from live registry.
        cfg = await AgentConfig.get(conn.config_id)
        await emit_change_event(
            actor_kind="human",
            actor_id=user_id,
            action="agent_config.update",
            resource_type="AgentConfig",
            resource_id=conn.config_id,
            before=await export_node(cfg) if cfg else None,
            after=await export_node(cfg) if cfg else None,
            scope=f"user:{user_id}",
        )

        return {"status": "unregistered", "agent_config_id": conn.config_id}

    return {"status": "not_registered"}


@endpoint(
    "/agentive/uplink/register-system",
    methods=["POST"],
    auth=False,
    tags=["Agentive"],
)
async def register_system_agent(
    request: Request,
    agent_type: AgentType = "jvagent",
    capabilities: Optional[List[str]] = None,
    uplink_url: str = "",
    persona: str = "",
    preferences: Optional[Dict[str, Any]] = None,
    jvagent_agent_id: Optional[str] = None,
    jvagent_base_url: Optional[str] = None,
) -> RegisterAgentResponse:
    """Register the deployment-wide agent. Service key only; no user context."""
    _require_service_key(request)

    capabilities = list(capabilities or [])
    preferences = dict(preferences or {})
    if jvagent_agent_id:
        preferences["jvagent_agent_id"] = jvagent_agent_id.strip()
    if jvagent_base_url:
        preferences["jvagent_base_url"] = jvagent_base_url.strip()

    # ``capabilities`` persists as inert descriptive metadata (ADR-003);
    # the former A2A catalogue-validation gate (I-A2A-01) was retired.

    now = datetime.now(timezone.utc).isoformat()

    existing = await AgentConfig.find_one(
        user_id="",
        scope="system",
        workspace_id=None,
    )

    prior_snapshot_sys = await export_node(existing) if existing else None  # D-03
    if existing:
        existing.agent_type = agent_type
        existing.capabilities = capabilities
        existing.uplink_url = uplink_url
        existing.persona = persona
        existing.is_active = True
        existing.last_connected_at = now
        existing.updated_at = now
        existing.user_id = ""
        existing.scope = "system"
        # Dual-write until ADR-003 facet collapse (Full Sweep F1).
        existing.facet = "system"
        existing.preferences = {**(existing.preferences or {}), **preferences}
        await existing.save()
        config = existing
        sys_action: ChangeEventAction = "agent_config.update"
        try:
            from app.agentive.services.agent_registry_node import (
                wire_agent_config_attachment_edge,
            )

            await wire_agent_config_attachment_edge(config)
        except Exception:
            import logging as _logging

            _logging.getLogger(__name__).warning(
                "register_system_agent: wire_agent_config_attachment_edge "
                "failed for %s",
                config.id,
            )
    else:
        from app.agentive.services.agent_registry_node import (
            register_agent_config,
        )

        config = await register_agent_config(
            actor_id="register-system",
            user_id="",
            scope="system",
            facet="system",
            agent_type=agent_type,
            capabilities=capabilities,
            uplink_url=uplink_url,
            persona=persona,
            is_active=True,
            workspace_id=None,
            preferences=preferences,
            last_connected_at=now,
        )
        sys_action = "agent_config.register"

    config_id = await uplink_registry.register(config)

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    # actor_kind="system" because /register-system is service-key-authenticated and
    # the deployment-wide agent has no per-user actor.
    await emit_change_event(
        actor_kind="system",
        actor_id="register-system",
        action=sys_action,
        resource_type="AgentConfig",
        resource_id=config.id,
        before=prior_snapshot_sys,
        after=await export_node(config),
        scope="user:system",
    )

    return RegisterAgentResponse(
        agent_config_id=config_id,
        status="registered",
        scope="system",
        capabilities=capabilities,
    )


@endpoint(
    "/agentive/uplink/heartbeat-system",
    methods=["GET"],
    auth=False,
    tags=["Agentive"],
)
async def agent_heartbeat_system(request: Request) -> Dict[str, Any]:
    """Heartbeat for the deployment-wide agent. Service key only."""
    _require_service_key(request)

    conn = await uplink_registry.get_system_agent()
    if conn:
        await uplink_registry.heartbeat(conn.config_id)
        return {"status": "alive", "agent_config_id": conn.config_id}

    cfg = await AgentConfig.find_one(user_id="", scope="system", workspace_id=None)
    if cfg and cfg.is_active:
        await uplink_registry.register(cfg)
        await uplink_registry.heartbeat(cfg.id)
        return {"status": "alive", "agent_config_id": cfg.id}

    return {"status": "not_registered"}
