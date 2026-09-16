"""Schemas for F3 Entitlement admin surface."""

from typing import List, Optional

from pydantic import BaseModel, Field


class EntitlementGrantRequest(BaseModel):
    workspace_id: str
    entitlement_key: str
    package_slug: Optional[str] = None
    expires_at: Optional[str] = None

    model_config = {"extra": "forbid"}


class EntitlementRevokeRequest(BaseModel):
    workspace_id: str
    entitlement_key: str

    model_config = {"extra": "forbid"}


class EntitlementResponse(BaseModel):
    id: str
    workspace_id: str
    entitlement_key: str
    package_slug: str
    status: str
    source: str
    on_loss: str
    data_access: str
    retention: str
    expires_at: Optional[str] = None
    revoked_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    model_config = {"extra": "forbid"}


class EntitlementRevokeResponse(BaseModel):
    entitlement_key: str
    workspace_id: str
    status: str
    paused_app_ids: List[str] = Field(default_factory=list)
    data_access: str
    retention: str

    model_config = {"extra": "forbid"}
