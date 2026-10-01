"""Schemas for agentive/api/agent_skills.py."""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class SkillComplianceWarning(BaseModel):
    code: str
    message: str


class SkillSummaryResponse(BaseModel):
    id: str
    source: Literal["core", "app", "workspace"]
    read_only: bool = False
    key: str = ""
    name: str = ""
    description: str = ""
    kind: str = "declarative"
    app_id: str = ""
    app_slug: str = ""
    namespaced_name: str = ""
    origin: str = "bundle"
    enabled: bool = True
    customized: bool = False
    stale_default: bool = False
    private: bool = False
    trust_tier: str = "untrusted"
    tools_required: List[str] = Field(default_factory=list)
    customized_at: Optional[str] = None
    customized_by: Optional[str] = None


class SkillListResponse(BaseModel):
    core: List[SkillSummaryResponse] = Field(default_factory=list)
    apps: List[SkillSummaryResponse] = Field(default_factory=list)
    workspace: List[SkillSummaryResponse] = Field(default_factory=list)
    total: int = 0


class SkillDetailResponse(SkillSummaryResponse):
    resolved_body: str = ""
    domain_body: str = ""
    bundle_default_body: str = ""
    prompt_template_ref: Optional[str] = None
    handler_ref: Optional[str] = None
    warnings: List[SkillComplianceWarning] = Field(default_factory=list)


class SkillCreateRequest(BaseModel):
    key: str
    name: str
    description: str = ""
    body_override: str
    tools_required: List[str] = Field(default_factory=list)


class SkillUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    body_override: Optional[str] = None
    tools_required: Optional[List[str]] = None
    enabled: Optional[bool] = None


class ToolCatalogueEntry(BaseModel):
    name: str
    friendly_label: str
    description: str = ""
    param_summary: str = ""


class ToolCatalogueResponse(BaseModel):
    tools: List[ToolCatalogueEntry] = Field(default_factory=list)
    total: int = 0


class EffectiveSkillEntry(BaseModel):
    id: str
    key: str
    name: str
    description: str = ""
    source: Literal["core", "app", "workspace"]
    app_id: Optional[str] = None
    app_name: Optional[str] = None
    state: Literal["available", "offer_first", "paused", "unavailable"]
    reason: Optional[str] = None
    tools_required: List[str] = Field(default_factory=list)


class EffectiveToolEntry(BaseModel):
    name: str
    description: str = ""
    source: Literal["core", "workspace"]


class EffectiveSkillAppChoice(BaseModel):
    id: str
    name: str


class EffectiveSkillContextResponse(BaseModel):
    workspace_id: str
    focused_app_id: Optional[str] = None
    apps: List[EffectiveSkillAppChoice] = Field(default_factory=list)
    skills: List[EffectiveSkillEntry] = Field(default_factory=list)
    tools: List[EffectiveToolEntry] = Field(default_factory=list)
