"""Authenticated resident assignment request and response contracts."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AssignmentState = Literal["assigned", "withdrawn"]
AssignmentReadiness = Literal[
    "paused", "needs_reassignment", "needs_task_setup", "awaiting_execution_policy"
]


class ResidentAssignmentRequest(BaseModel):
    """Set desired state at an observed assignment revision."""

    model_config = ConfigDict(extra="forbid")
    state: AssignmentState
    expected_assignment_revision: int = Field(ge=0, strict=True)


class ResidentAssignmentResponse(BaseModel):
    """Public assignment state without execution authority."""

    state: AssignmentState
    revision: int


class ResidentAssignmentView(ResidentAssignmentResponse):
    """State and current control availability for an Entry reader."""

    can_assign: bool
    can_withdraw: bool
    readiness: AssignmentReadiness | None = None


class ResidentAssignmentSettings(BaseModel):
    """Opt in to recording resident assignments for this workspace."""

    model_config = ConfigDict(extra="forbid")
    enabled: bool = Field(strict=True)
