"""Typed contracts for the durable work kernel."""

from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

WorkStatus = Literal[
    "queued",
    "running",
    "waiting_for_human",
    "waiting_for_event",
    "retry_wait",
    "succeeded",
    "failed",
    "cancelled",
    "expired",
    "dead_letter",
]

WorkKind = Literal[
    "capability",
    "routine_turn",
    "approval_resume",
    "event_trigger",
    "migration",
    "app_lifecycle",
]

FailureClass = Literal[
    "transient",
    "rate_limited",
    "dependency_unavailable",
    "permanent",
    "policy_denied",
    "cancelled",
    "deadline_exceeded",
    "non_replayable",
]

WorkApprovalStatus = Literal["pending", "approved", "rejected", "expired"]

LEGAL_WORK_TRANSITIONS: Dict[WorkStatus, frozenset[WorkStatus]] = {
    "queued": frozenset({"running", "failed", "cancelled", "expired"}),
    "running": frozenset(
        {
            "waiting_for_human",
            "waiting_for_event",
            "retry_wait",
            "succeeded",
            "failed",
            "cancelled",
            "expired",
            "dead_letter",
        }
    ),
    "waiting_for_human": frozenset({"queued", "failed", "cancelled", "expired"}),
    "waiting_for_event": frozenset({"queued", "failed", "cancelled", "expired"}),
    "retry_wait": frozenset(
        {"queued", "failed", "cancelled", "expired", "dead_letter"}
    ),
    "succeeded": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
    "expired": frozenset(),
    "dead_letter": frozenset(),
}

TERMINAL_WORK_STATUSES = frozenset(
    {"succeeded", "failed", "cancelled", "expired", "dead_letter"}
)


class WorkError(Exception):
    """Normalized durable-work failure."""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        self.message = message or code
        super().__init__(self.code, self.message)


class RetryPolicy(BaseModel):
    """Bounded exponential backoff with deterministic jitter."""

    max_attempts: int = 5
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 300.0
    jitter_ratio: float = 0.1

    model_config = ConfigDict(extra="forbid")

    @field_validator("max_attempts")
    @classmethod
    def _positive_attempts(cls, value: int) -> int:
        if value < 1:
            raise ValueError("max_attempts must be >= 1")
        return value

    @field_validator("base_delay_seconds", "max_delay_seconds")
    @classmethod
    def _positive_delay(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("delay seconds must be > 0")
        return value

    @field_validator("jitter_ratio")
    @classmethod
    def _bounded_jitter(cls, value: float) -> float:
        if value < 0 or value > 0.5:
            raise ValueError("jitter_ratio must be in [0, 0.5]")
        return value

    @model_validator(mode="after")
    def _max_ge_base(self) -> "RetryPolicy":
        if self.max_delay_seconds < self.base_delay_seconds:
            raise ValueError("max_delay_seconds must be >= base_delay_seconds")
        return self


class WorkFailure(BaseModel):
    """Normalized failure record stored on WorkItem / returned to callers."""

    class_: FailureClass = Field(alias="class")
    code: str
    message: str = ""
    retryable: bool = False

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class WorkExecutionContext(BaseModel):
    """Immutable host context propagated through effect boundaries."""

    work_item_id: str
    attempt: int
    run_id: str
    principal_id: str
    workspace_id: str
    logical_step_key: str
    effect_key: str
    lease_token: str
    lease_fence: int
    deadline_at: Optional[str] = None
    cancellation_signal: bool = False

    model_config = ConfigDict(extra="forbid")


class EnqueueWorkRequest(BaseModel):
    """Validated enqueue input used by tests and service callers."""

    kind: WorkKind
    origin: str
    principal_id: str
    workspace_id: str
    idempotency_key: str
    input_payload: Dict[str, Any] = Field(default_factory=dict)
    plan_revision: Optional[str] = None
    plan: Dict[str, Any] = Field(default_factory=dict)
    dependency_work_item_ids: list[str] = Field(default_factory=list)
    precommit_draft: Dict[str, Any] = Field(default_factory=dict)
    remaining_obligations: list[Dict[str, Any]] = Field(default_factory=list)
    thread_id: Optional[str] = None
    app_id: Optional[str] = None
    definition_id: Optional[str] = None
    parent_work_item_id: Optional[str] = None
    causation_id: Optional[str] = None
    deadline_at: Optional[str] = None
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)

    model_config = ConfigDict(extra="forbid")


__all__ = [
    "EnqueueWorkRequest",
    "FailureClass",
    "LEGAL_WORK_TRANSITIONS",
    "RetryPolicy",
    "TERMINAL_WORK_STATUSES",
    "WorkApprovalStatus",
    "WorkError",
    "WorkExecutionContext",
    "WorkFailure",
    "WorkKind",
    "WorkStatus",
]
