"""Schemas for agentive/api/uplink.py — agent registration + heartbeat bodies."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.agentive.types import AgentType


class RegisterAgentRequest(BaseModel):
    """User-context agent registration body (BYOA personal/org-facing scope)."""

    scope: str = "personal"
    agent_type: AgentType = "jvagent"
    capabilities: List[str] = Field(default_factory=list)
    uplink_url: str = ""
    persona: str = ""
    workspace_id: Optional[str] = None
    preferences: Dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


class RegisterSystemAgentRequest(BaseModel):
    """Service-key-only deployment-wide agent registration body.

    ``jvagent_agent_id`` and ``jvagent_base_url`` are jvagent-specific top-level
    fields preserved from the existing wire shape (uplink.py:140-152). They
    are folded into ``preferences`` server-side so the typed boundary stays
    vendor-neutral while preserving back-compat with current callers.
    """

    agent_type: AgentType = "jvagent"
    capabilities: List[str] = Field(default_factory=list)
    uplink_url: str = ""
    persona: str = ""
    preferences: Dict[str, Any] = Field(default_factory=dict)
    jvagent_agent_id: Optional[str] = None
    jvagent_base_url: Optional[str] = None

    model_config = {"extra": "forbid"}


class RegisterAgentResponse(BaseModel):
    """Response shape for register / register-system."""

    agent_config_id: str
    status: str
    capabilities: List[str] = Field(default_factory=list)
    scope: Optional[str] = None
