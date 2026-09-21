"""App-bundled Agent Pydantic schemas — Phase 10 / Plan 10-04.

Wire shape for ``app.agentive.services.uplink_registry.register_app_agent`` and
introspection responses. Plan 10-05's install lifecycle service calls
``register_app_agent(...)`` once per ``app.agents[]`` manifest entry on install.

The agent itself persists as an ``AgentConfig`` Node with ``app_id`` set
(additive field — see ``app.agentive.nodes.AgentConfig``).
"""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class ScheduledRunSpec(BaseModel):
    """Per-schedule descriptor captured on AgentConfig.scheduled_runs.

    ``status`` is set at registration time:
      - ``scheduled``: native scheduler accepted the registration.
      - ``manual``: scheduler unavailable; agent runs on user trigger only.
        Degradation path per app_bundles_v1.md §6.4.
    """

    cron: str = ""
    description: str = ""
    status: Literal["scheduled", "manual"] = "manual"


class AppBundledAgentRegisterRequest(BaseModel):
    """One agent spec as it arrives at ``register_app_agent``.

    Mirrors ``operational_model_runtime._parse_manifest_agents`` output.
    """

    key: str
    name: Optional[str] = None
    description: str = ""
    persona_ref: Optional[str] = None
    persona: str = ""
    scope: Literal["app", "workspace"] = "app"
    staging: Literal["required", "optional", "none"] = "required"
    skills: List[str] = Field(default_factory=list)  # references skill_registry keys
    capabilities: List[str] = Field(default_factory=list)  # raw MCP tool names
    default_schedules: List[ScheduledRunSpec] = Field(default_factory=list)


class AppBundledAgentOut(BaseModel):
    """Response shape for a single App-bundled AgentConfig."""

    id: str
    app_id: Optional[str] = None
    workspace_id: Optional[str] = None
    scope: str
    agent_type: str
    persona: str
    capabilities: List[str] = Field(default_factory=list)
    staging: str = "required"
    scheduled_runs: List[Dict[str, Any]] = Field(default_factory=list)
    is_active: bool
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class AppBundledAgentListResponse(BaseModel):
    """Response shape for App-bundled agent listing."""

    agents: List[AppBundledAgentOut]
    total: int
