"""Host price evidence for an exact model operation, never a model-issued tariff."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from pydantic import Field, model_validator

from app.agentive.harness.contracts import HarnessExecutionScope
from app.schemas.agentive.work_budget import Money
from app.schemas.agentive.work_mandate import (
    CanonicalKey,
    MandateContract,
    MandateModelRoute,
)


class ModelPriceRequest(MandateContract):
    scope: HarnessExecutionScope
    route: MandateModelRoute
    request_id: CanonicalKey
    input_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    input_tokens_upper: int = Field(ge=1, le=2_000_000, strict=True)
    output_tokens_upper: int = Field(ge=1, le=2_000_000, strict=True)
    bounds_ref: CanonicalKey
    bounds_valid_until: datetime

    @model_validator(mode="after")
    def _aware(self) -> "ModelPriceRequest":
        if self.bounds_valid_until.utcoffset() is None:
            raise ValueError("model bounds must have an aware expiry")
        return self

    def digest(self) -> str:
        from datetime import timezone

        value = self.model_dump(mode="json")
        value["bounds_valid_until"] = self.bounds_valid_until.astimezone(
            timezone.utc
        ).isoformat()
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


class ModelTokenPriceEvidence(MandateContract):
    """Trusted host attests applicability; Core computes the money ceiling.

    Rates include reasoning/output and all billed input, ignoring cache or
    off-peak discounts. A required fee prevents assuming unlisted fees are zero.
    This is admission evidence, not a provider charge or invoice receipt.
    """

    request_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    policy_ref: CanonicalKey
    source_ref: CanonicalKey
    account_terms_ref: CanonicalKey
    route: MandateModelRoute
    valid_from: datetime
    valid_until: datetime
    input_usd_per_million: Money
    output_usd_per_million: Money
    request_fee_upper_usd: Money

    @model_validator(mode="after")
    def _window(self) -> "ModelTokenPriceEvidence":
        if (
            self.valid_from.utcoffset() is None
            or self.valid_until.utcoffset() is None
            or self.valid_until <= self.valid_from
        ):
            raise ValueError("price evidence needs an aware nonempty validity window")
        return self
