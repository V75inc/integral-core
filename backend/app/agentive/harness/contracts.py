"""Immutable identity passed into one native Harness invocation.

These values are constructed from authenticated Integral state. They are not
accepted from model output and do not replace request/thread authorization.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)


class HarnessExecutionScope(BaseModel):
    """Trusted IDs and revisions that fence one Harness execution.

    In Integral Core, the workspace is the tenant boundary. The authenticated
    principal may have direct or inherited access; authorization is resolved
    before constructing this value. ``session_id`` and ``run_id`` are generated
    and persisted by Core, then mapped to the framework's opaque identifiers.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    tenant_id: str
    principal_id: str
    workspace_id: str
    thread_id: str
    session_id: str
    run_id: str
    permission_revision: str
    capability_version: str

    @field_validator(
        "tenant_id",
        "principal_id",
        "workspace_id",
        "thread_id",
        "session_id",
        "run_id",
        "permission_revision",
        "capability_version",
    )
    @classmethod
    def _canonical_nonempty_value(cls, value: str) -> str:
        """Reject empty or padded keys so persisted identity has one spelling."""
        if not value.strip():
            raise ValueError("Harness execution scope values cannot be empty")
        if value != value.strip():
            raise ValueError(
                "Harness execution scope values cannot be whitespace-padded"
            )
        return value

    @model_validator(mode="after")
    def _validate_workspace_tenant(self) -> "HarnessExecutionScope":
        """Keep the session namespace aligned to the authorized workspace."""
        if self.tenant_id != self.workspace_id:
            raise ValueError(
                "Integral tenant scope must match the authorized workspace"
            )
        return self

    @property
    def framework_conversation_id(self) -> str:
        """Return the server-owned Harness conversation mapping."""
        return self.session_id

    @property
    def framework_run_id(self) -> str:
        """Return the unique server-owned Harness invocation mapping."""
        return self.run_id


class ResolvedModelRoute(BaseModel):
    """Per-run LiteLLM route resolved from trusted Core configuration.

    This is transient runtime state. Do not persist or log it: the API key is
    present only for the model adapter's SDK call and is masked by Pydantic's
    secret type when represented.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    provider: str
    model: str
    api_base: str | None = None
    api_key: SecretStr | None = None
    ollama_num_ctx: int | None = Field(default=None, ge=512, le=131072)
    ollama_num_predict: int | None = Field(default=None, ge=1, le=131072)
    ollama_think: str | bool | None = None
    ollama_clear_thinking: bool | None = None
    credential_source: Literal["workspace_byok", "platform", "local"]
    credential_ref: str | None = None

    @field_validator("provider", "model")
    @classmethod
    def _route_component_nonempty(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("model route components must be canonical and nonempty")
        return value

    @field_validator("credential_ref")
    @classmethod
    def _credential_ref_canonical(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or value != value.strip()):
            raise ValueError("credential reference must be canonical and nonempty")
        return value

    @field_validator("api_base")
    @classmethod
    def _api_base_canonical(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or value != value.strip()):
            raise ValueError("model API base must be canonical and nonempty")
        return value

    @model_validator(mode="after")
    def _validate_key_source(self) -> "ResolvedModelRoute":
        if (
            self.ollama_num_ctx is not None
            or self.ollama_num_predict is not None
            or self.ollama_think is not None
            or self.ollama_clear_thinking is not None
        ) and (self.provider != "ollama_chat" or self.credential_source != "local"):
            raise ValueError(
                "Ollama generation settings are valid only for local Ollama routes"
            )
        if self.credential_source == "workspace_byok":
            if self.api_key is None or not self.api_key.get_secret_value():
                raise ValueError("selected workspace model credential is missing")
        elif self.credential_source == "platform":
            # Deployment credentials remain in LiteLLM's configured environment;
            # Integral does not copy them into the per-run route contract.
            if self.api_key is not None:
                raise ValueError("platform credentials must remain environment-owned")
        elif self.api_key is not None:
            raise ValueError("local model routes must not carry an API key")
        return self


class ModelUsageObservation(BaseModel):
    """Normalized usage facts from one physical model response.

    Token quantities are source-reported when present. Cost is nullable and
    explicitly sourced: LiteLLM may report it, the provider may return it, or
    Core may calculate it from LiteLLM's model pricing. A calculated amount is
    an estimate, not an invoice amount.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    provider_cost_usd: Decimal | None = Field(default=None, ge=Decimal("0"))
    # Preserve SDK accounting even when its zero is an unpriced-route
    # placeholder rather than evidence of a free provider response.
    litellm_response_cost_usd: Decimal | None = Field(default=None, ge=Decimal("0"))
    cost_source: Literal[
        "litellm_response",
        "provider_response",
        "litellm_calculated",
        "unavailable",
    ]
    complete: bool


class ModelRequestContextObservation(BaseModel):
    """Content-free dimensions of the actual outbound request, in characters.

    These are diagnostic sizes, not billed token estimates. Instructions include
    the framework-rendered catalogue and active skills; conversation includes
    user/assistant messages and tool arguments; tool results/schemas are separate.
    No prompt, record data, credentials or model reasoning is stored here.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    instruction_chars: int = Field(ge=0)
    conversation_chars: int = Field(ge=0)
    tool_result_chars: int = Field(ge=0)
    tool_schema_chars: int = Field(ge=0)
    message_count: int = Field(ge=0)
    visible_tool_count: int = Field(ge=0)


class PhysicalModelRequest(BaseModel):
    """Immutable identity and outcome for exactly one outbound SDK attempt."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    request_id: str
    scope: HarnessExecutionScope
    provider: str
    model: str
    attempt: int = Field(ge=1)
    dispatched_at: datetime
    observed_at: datetime | None = None
    evidence_source: Literal["adapter", "litellm_callback", "reconciliation"] = (
        "adapter"
    )
    outcome: Literal[
        "dispatch_intent",
        "responded",
        "failed",
        "cancelled",
        "outcome_unknown",
    ]
    provider_request_id: str | None = None
    usage: ModelUsageObservation | None = None
    request_context: ModelRequestContextObservation | None = None

    @field_validator("request_id", "provider", "model")
    @classmethod
    def _required_canonical_string(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError(
                "model request identity fields must be canonical and nonempty"
            )
        return value

    @field_validator("provider_request_id")
    @classmethod
    def _provider_request_id_canonical(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or value != value.strip()):
            raise ValueError("provider request ID must be canonical and nonempty")
        return value


class RunUsageSummary(BaseModel):
    """Observed model usage for one run; never a commercial invoice amount."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    request_count: int = Field(ge=0)
    completed_request_count: int = Field(ge=0)
    unresolved_request_count: int = Field(ge=0)
    reported_input_tokens: int = Field(ge=0)
    reported_output_tokens: int = Field(ge=0)
    provider_cost_usd: Decimal | None = Field(default=None, ge=Decimal("0"))
    token_usage_complete: bool
    provider_cost_complete: bool


class HarnessCheckpointManifest(BaseModel):
    """Core recovery metadata paired with one encrypted framework snapshot.

    The message snapshot is stored by Pydantic AI Harness. This manifest records
    the Core authority and recovery obligations that the framework snapshot
    cannot represent. It contains identifiers and policy metadata only; it is
    not a second transcript or a source of authorization.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    scope: HarnessExecutionScope
    execution_fence_id: str
    framework_snapshot_id: str
    snapshot_step_index: int = Field(ge=0)
    snapshot_state: Literal["complete", "interrupted"]
    safe_for_resume: bool
    framework_codec_version: str
    capability_fingerprint: str
    capability_restore_policies: dict[
        str, Literal["restore", "rebuild", "discard", "block"]
    ]
    plan_revision: str
    pending_obligation_ids: list[str] = Field(default_factory=list)
    tool_effect_receipt_ids: list[str] = Field(default_factory=list)
    approval_ids: list[str] = Field(default_factory=list)
    model_request_ids: list[str] = Field(default_factory=list)
    external_artifact_refs: list[str] = Field(default_factory=list)
    usage_reconciled: bool = False

    @field_validator(
        "framework_snapshot_id",
        "execution_fence_id",
        "framework_codec_version",
        "capability_fingerprint",
        "plan_revision",
    )
    @classmethod
    def _manifest_identity_nonempty(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("checkpoint manifest identity fields must be canonical")
        return value

    @model_validator(mode="after")
    def _validate_run_fence(self) -> "HarnessCheckpointManifest":
        if self.execution_fence_id != self.scope.run_id:
            raise ValueError("checkpoint execution fence must match its Core run")
        return self

    @field_validator(
        "pending_obligation_ids",
        "tool_effect_receipt_ids",
        "approval_ids",
        "model_request_ids",
        "external_artifact_refs",
    )
    @classmethod
    def _manifest_references_are_canonical(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or value != value.strip() for value in values):
            raise ValueError("checkpoint references must be canonical nonempty IDs")
        if len(set(values)) != len(values):
            raise ValueError("checkpoint references must be unique")
        return values
