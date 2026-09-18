"""Capability / evidence contracts for intrinsic agentive queryability (ADR-012)."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

CapabilityKind = Literal[
    "resource",
    "query",
    "operation",
    "view",
    "skill",
    "schedule",
    "event",
]
EffectClass = Literal["read", "propose", "execute"]
AvailabilityState = Literal[
    "active",
    "paused",
    "unavailable",
    "activation_failed",
]


class ObjectRef(BaseModel):
    """Canonical reference to a governed substrate object."""

    kind: str
    id: str
    workspace_id: Optional[str] = None
    app_id: Optional[str] = None
    title: Optional[str] = None
    url_path: Optional[str] = None


class Evidence(BaseModel):
    """Provenance / grounding metadata for a query or operation result."""

    object_refs: List[ObjectRef] = Field(default_factory=list)
    schema_version: str = "1.0"
    package_slug: Optional[str] = None
    package_version: Optional[str] = None
    catalogue_generation: Optional[str] = None
    policy_decision_id: Optional[str] = None
    audit_correlation_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    freshness: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)
    applied_scope: Optional[str] = None


class CapabilityDescriptor(BaseModel):
    """Permission-filterable capability advertisement."""

    namespace: str
    key: str
    version: str = "1.0"
    owner_package: str = "integral-core"
    kind: CapabilityKind
    name: str
    description: str = ""
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    output_schema: Dict[str, Any] = Field(default_factory=dict)
    effects: EffectClass = "read"
    resources: List[str] = Field(default_factory=list)
    policy_action: Optional[str] = None
    availability: AvailabilityState = "active"
    trust_tier: Optional[str] = None
    app_id: Optional[str] = None
    discoverable: bool = True
    documentation_refs: List[str] = Field(default_factory=list)

    @property
    def capability_id(self) -> str:
        return f"{self.namespace}.{self.key}"


class CapabilityCatalogueSnapshot(BaseModel):
    workspace_id: str
    generation_id: str
    compiled_at: str
    capabilities: List[CapabilityDescriptor] = Field(default_factory=list)
    diagnostics: List[str] = Field(default_factory=list)
