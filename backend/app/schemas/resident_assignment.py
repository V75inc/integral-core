"""Authenticated resident assignment request and response contracts."""

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

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
    can_request_review: bool = False


class ResidentAssignmentSettings(BaseModel):
    """Opt in to recording resident assignments for this workspace."""

    model_config = ConfigDict(extra="forbid")
    enabled: bool = Field(strict=True)


class ResidentAssignmentTaskSnapshot(BaseModel):
    """Private review input captured from an exact live assignment."""

    model_config = ConfigDict(frozen=True)
    workspace_id: str
    track_id: str
    entry_id: str
    assignment_revision: int
    sponsor_principal_id: str
    resident_provider_id: str
    intake_fact_id: str
    entry_revision: int
    entry_type_id: str
    definition_id: str
    definition_revision: int
    definition_digest: str
    outcome: str
    acceptance_criteria: JsonValue = None
    due_date: JsonValue = None
    dependencies: JsonValue = None

    def fingerprint(self) -> str:
        """Return the versioned identity to bind to a review or work admission."""
        payload = json.dumps(
            ["resident-assignment-task-v1", self.model_dump(mode="json")],
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return hashlib.sha256(payload.encode()).hexdigest()


class ResidentAssignmentReviewRequest(BaseModel):
    """Request private preparation for the current assignment revision."""

    model_config = ConfigDict(extra="forbid")
    expected_assignment_revision: int = Field(ge=1, strict=True)


class ResidentAssignmentReviewDecision(BaseModel):
    """Approve the exact review shown to the authenticated caller."""

    model_config = ConfigDict(extra="forbid")
    review_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class ResidentAssignmentReviewView(BaseModel):
    """Review projection for the current sponsor or a workspace reviewer."""

    work_item_id: str
    review_digest: str
    status: str
    approval_status: str
    can_approve: bool
    model: str
    provider: str
    max_model_requests: int
    max_spend_usd: str
    deadline_at: str
    preparation_thread_id: str | None = None
    needs_model_evidence: bool = False


class ResidentAssignmentPlacementView(BaseModel):
    """Receipt for an existing pending Entry write proposal."""

    proposal_token: str
