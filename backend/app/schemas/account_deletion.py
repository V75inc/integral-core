"""Request/response models for self-service account deletion."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class AccountDeletionBlockerResource(BaseModel):
    """A single owned resource blocking account deletion."""

    type: str
    id: str
    title: str = ""


class AccountDeletionBlocker(BaseModel):
    """Structured blocker returned when deletion cannot proceed."""

    code: str
    message: str
    resources: List[AccountDeletionBlockerResource] = Field(default_factory=list)
    workspace_id: Optional[str] = None
    workspace_name: Optional[str] = None


class PersonalWorkspaceSummary(BaseModel):
    """Counts for personal workspace content that will be deleted."""

    workspace_id: str
    name: str = ""
    app_count: int = 0
    track_count: int = 0


class OrgMembershipSummary(BaseModel):
    """Organization workspace membership that will be removed."""

    workspace_id: str
    name: str = ""
    role: str = ""


class AccountDeletionPreview(BaseModel):
    """Pre-flight impact summary for account deletion."""

    can_delete: bool
    email: str = ""
    blockers: List[AccountDeletionBlocker] = Field(default_factory=list)
    personal_workspaces: List[PersonalWorkspaceSummary] = Field(default_factory=list)
    org_memberships: List[OrgMembershipSummary] = Field(default_factory=list)
    impact: List[str] = Field(default_factory=list)


class AccountDeletionRequest(BaseModel):
    """Body for DELETE /users/{user_id}."""

    confirm_email: str


class AccountDeletionResponse(BaseModel):
    """Successful account deletion response."""

    message: str
    deleted_user_id: str


class AccountDeletionResult(BaseModel):
    """Internal result from the lifecycle orchestrator."""

    deleted_user_id: str
    deleted_user_node_id: str
    prior_snapshot: Dict[str, Any]
