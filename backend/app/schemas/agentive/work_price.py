"""Host price evidence for an exact model operation, never a model-issued tariff."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import ROUND_CEILING, Decimal, localcontext

from pydantic import Field, model_validator

from app.agentive.harness.contracts import HarnessExecutionScope
from app.schemas.agentive.work_mandate import (
    CanonicalKey,
    MandateContract,
    MandateModelRoute,
)
from app.schemas.agentive.work_money import Money


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


class ModelPriceBinding(MandateContract):
    """Retained host evidence for an exact bounded physical request."""

    request: ModelPriceRequest
    evidence: ModelTokenPriceEvidence

    @model_validator(mode="after")
    def _bound(self) -> "ModelPriceBinding":
        if (
            self.evidence.request_digest != self.request.digest()
            or self.evidence.route != self.request.route
        ):
            raise ValueError("price evidence must match the exact request")
        return self

    def canonical_payload(self) -> dict:
        request = self.request.model_dump(mode="json")
        request["bounds_valid_until"] = self.request.bounds_valid_until.astimezone(
            timezone.utc
        ).isoformat()
        evidence = self.evidence.model_dump(mode="json")
        for field in (
            "input_usd_per_million",
            "output_usd_per_million",
            "request_fee_upper_usd",
        ):
            evidence[field] = format(getattr(self.evidence, field), ".8f")
        for field in ("valid_from", "valid_until"):
            evidence[field] = (
                getattr(self.evidence, field).astimezone(timezone.utc).isoformat()
            )
        return {"request": request, "evidence": evidence}

    def quote_fields(self) -> dict:
        with localcontext() as context:
            context.prec = 48
            ceiling = (
                Decimal(self.request.input_tokens_upper)
                * self.evidence.input_usd_per_million
                + Decimal(self.request.output_tokens_upper)
                * self.evidence.output_usd_per_million
            ) / Decimal(1_000_000) + self.evidence.request_fee_upper_usd
            ceiling = ceiling.quantize(Decimal("0.00000001"), rounding=ROUND_CEILING)
        identity = hashlib.sha256(
            json.dumps(
                self.canonical_payload()["evidence"],
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        return {
            "provider": self.request.route.provider,
            "model": self.request.route.model,
            "credential_ref": self.request.route.credential_ref,
            "quote_ref": "host-model-price:" + identity,
            "valid_until": min(
                self.request.bounds_valid_until, self.evidence.valid_until
            ),
            "upper_cost": ceiling,
        }
