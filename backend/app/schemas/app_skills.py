"""App-bundled Skill Pydantic schemas — Phase 10 / Plan 10-04.

Wire shape for ``app.agentive.services.skill_registry`` registration calls and
introspection responses. Plan 10-05's install lifecycle service calls
``register_skill(...)`` with one ``SkillRegisterRequest`` per declared skill;
the response uses ``SkillOut`` / ``SkillListResponse``.

Defined at the Pydantic boundary so the Skill Node's ``Literal`` fields are
enforced wire-side without round-tripping through the Node class (CLAUDE.md
Pydantic boundary convention).
"""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class SkillRegisterRequest(BaseModel):
    """One skill spec as it arrives at ``skill_registry.register_skill``.

    Mirrors the normalized output of
    ``content_profile_runtime._parse_manifest_skills``. The lifecycle service
    builds one per ``app.skills[]`` entry on install.
    """

    key: str
    name: Optional[str] = None
    description: str = ""
    kind: Literal["declarative", "custom"] = "declarative"
    prompt_template_ref: Optional[str] = None
    handler_ref: Optional[str] = None
    tools_required: List[str] = Field(default_factory=list)
    parameters_schema: Dict[str, Any] = Field(default_factory=dict)
    outputs: List[Dict[str, Any]] = Field(default_factory=list)
    private: bool = False
    trust_tier: Literal["untrusted", "trusted"] = "untrusted"
    external_apis: List[str] = Field(default_factory=list)


class SkillOut(BaseModel):
    """Response shape for a single registered Skill."""

    id: str
    app_id: str
    workspace_id: str
    key: str
    name: str
    description: str
    kind: Literal["declarative", "custom"]
    prompt_template_ref: Optional[str] = None
    handler_ref: Optional[str] = None
    tools_required: List[str] = Field(default_factory=list)
    parameters_schema: Dict[str, Any] = Field(default_factory=dict)
    outputs: List[Dict[str, Any]] = Field(default_factory=list)
    private: bool
    trust_tier: Literal["untrusted", "trusted"]
    external_apis: List[str] = Field(default_factory=list)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class SkillListResponse(BaseModel):
    """Response shape for ``GET ... /skills`` style introspection."""

    skills: List[SkillOut]
    total: int
