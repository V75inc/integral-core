"""Transient, credential-free physical SDK input for trusted host admission."""

from __future__ import annotations

import hashlib
from datetime import datetime

from pydantic import Field, model_validator

from app.agentive.harness.contracts import HarnessExecutionScope, ModelRouteIdentity
from app.schemas.agentive.work_mandate import CanonicalKey, MandateContract


class ModelDispatchInput(MandateContract):
    """Exact final SDK arguments; retained evidence stores only their digest.

    Credential values and endpoint URLs never enter this object. The endpoint
    digest and opaque credential generation bind the transport destination.
    Messages, tools and provider options remain transient host input, not logs.
    """

    scope: HarnessExecutionScope
    route: ModelRouteIdentity
    request_id: CanonicalKey
    sdk_payload_json: str = Field(repr=False)
    endpoint_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")

    def fingerprint(self) -> str:
        return hashlib.sha256(
            (self.endpoint_fingerprint + ":" + self.sdk_payload_json).encode()
        ).hexdigest()


class ModelPayloadBounds(MandateContract):
    """Host attests ceilings for all billed input and output, including reasoning.

    A token count estimate or context-window setting is insufficient. The host
    must understand the selected provider's actual limits and every payload
    modality. Unsupported routes/options require rejection, never a fallback.
    """

    input_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    input_tokens_upper: int = Field(ge=1, le=2_000_000, strict=True)
    output_tokens_upper: int = Field(ge=1, le=2_000_000, strict=True)
    bounds_ref: CanonicalKey
    valid_until: datetime

    @model_validator(mode="after")
    def _aware(self) -> "ModelPayloadBounds":
        if self.valid_until.utcoffset() is None:
            raise ValueError("model payload bounds require an aware expiry")
        return self
