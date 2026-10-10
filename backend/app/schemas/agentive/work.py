"""Typed contracts for the durable work kernel."""

from __future__ import annotations

import json
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
    "chat_turn",
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


class ChatTurnHostControl(BaseModel):
    """Server-captured host event bound to an authoritative source revision."""

    action: Literal["prompt_sheet_resume", "staging_follow_through"]
    source_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    read_only: bool = False

    model_config = ConfigDict(extra="forbid", frozen=True)


class ChatTurnSubmissionRequest(BaseModel):
    """Trusted server-side envelope for an idempotent user chat submission."""

    principal_id: str = Field(min_length=1, max_length=255)
    workspace_id: str = Field(min_length=1, max_length=255)
    thread_id: str = Field(min_length=1, max_length=255)
    client_request_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    parts: list[Dict[str, Any]] = Field(min_length=1)
    provider_metadata: Dict[str, Any] = Field(default_factory=dict)
    parent_id: Optional[str] = None
    execution_context: Optional["ChatTurnExecutionContext"] = None
    # Computed by the authenticated HTTP producer from the validated client
    # payload. It never comes from an arbitrary browser-supplied digest.
    client_payload_digest: Optional[str] = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )

    model_config = ConfigDict(extra="forbid", frozen=True)


class ChatTurnExecutionContext(BaseModel):
    """Bounded trusted host context kept outside the canonical user message."""

    system_context: str = Field(default="", max_length=64_000)
    no_workspace_writes: bool = False
    design_only: bool = False
    focused_track_id: Optional[str] = Field(default=None, max_length=255)
    focused_space_id: Optional[str] = Field(default=None, max_length=255)
    focused_view_id: Optional[str] = Field(default=None, max_length=255)
    extra_data: Dict[str, Any] = Field(default_factory=dict)
    host_control: Optional[ChatTurnHostControl] = None
    attachment_bindings: list[Dict[str, Any]] = Field(
        default_factory=list, max_length=10
    )

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("extra_data")
    @classmethod
    def _bounded_allowlisted_extra_data(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        allowed = {
            "agent_id",
            "entities_referenced",
            "page_context",
            "pending_approvals",
            "pending_approvals_marker",
            "run_id",
        }
        if set(value) - allowed:
            raise ValueError("execution context contains unsupported host fields")
        try:
            encoded = json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("execution context extra_data must be JSON-safe") from exc
        if len(encoded.encode("utf-8")) > 192_000:
            raise ValueError("execution context extra_data exceeds its size limit")
        return value


ChatTurnSubmissionRequest.model_rebuild()


class ChatTurnSubmissionReceipt(BaseModel):
    """Non-content receipt for one accepted message and durable work item."""

    client_request_id: str
    message_id: str
    work_item_id: str
    status: WorkStatus

    model_config = ConfigDict(extra="forbid", frozen=True)


class WorkFailure(BaseModel):
    """Normalized failure record stored on WorkItem / returned to callers."""

    class_: FailureClass = Field(alias="class")
    code: str
    message: str = ""
    retryable: bool = False

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @classmethod
    def from_record(
        cls, record: object, *, default_class: FailureClass = "permanent"
    ) -> "WorkFailure":
        """Project legacy errors into the bounded public failure contract.

        Older chat workers persisted code/message without a failure class.
        Do not rewrite those historical rows or expose arbitrary error details.
        Invalid shapes remain a non-retryable failure with a safe explanation.
        """
        from pydantic import ValidationError

        if isinstance(record, dict):
            fields = {
                key: record[key]
                for key in ("class", "code", "message", "retryable")
                if key in record
            }
            fields.setdefault("class", default_class)
            try:
                return cls.model_validate(fields)
            except ValidationError:
                pass
        return cls(
            class_=default_class,
            code="work.failure_record_invalid",
            message="Failure details are unavailable for this work item.",
            retryable=False,
        )


class WorkExecutionContext(BaseModel):
    """Immutable, server-derived lease authority propagated to effect boundaries."""

    work_item_id: str
    attempt: int = Field(ge=1)
    run_id: str
    principal_id: str
    workspace_id: str
    thread_id: str
    logical_step_key: str
    effect_key: str
    lease_token: str
    lease_fence: int = Field(ge=1)
    deadline_at: Optional[str] = None
    cancellation_signal: bool = False

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    @field_validator(
        "work_item_id",
        "run_id",
        "principal_id",
        "workspace_id",
        "logical_step_key",
        "effect_key",
        "lease_token",
    )
    @classmethod
    def _canonical_nonempty_authority(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("WorkItem execution authority values must be canonical")
        return value


class WorkItemStatusResponse(BaseModel):
    """Safe, caller-visible projection of a durable work item.

    Work input and plans can contain credentials or lifecycle tokens. The
    observation contract deliberately exposes only state, identifiers, and
    normalized completion/failure references.
    """

    work_item_id: str
    kind: str
    operation: str = ""
    status: WorkStatus
    workspace_id: str
    app_id: str = ""
    attempt: int = 0
    next_attempt_at: str = ""
    updated_at: str = ""
    result_refs: list[str] = Field(default_factory=list)
    failure: Optional[WorkFailure] = None

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
    "WorkItemStatusResponse",
    "WorkStatus",
]
