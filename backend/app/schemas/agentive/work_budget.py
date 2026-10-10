"""Host-resolved, exact reservation inputs; never a model-issued price grant."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Annotated

from pydantic import Field, model_validator

from app.schemas.agentive.work_mandate import (
    CanonicalKey,
    MandateCapabilityGrant,
    MandateContract,
    MandateExternalGrant,
    MandateModelRoute,
)
from app.schemas.agentive.work_money import Money, money_units
from app.schemas.agentive.work_price import ModelPriceBinding


class MandateCostQuote(MandateContract):
    """Trusted host price resolver output; this schema does not verify pricing."""

    provider: CanonicalKey
    model: CanonicalKey
    credential_ref: CanonicalKey
    quote_ref: CanonicalKey
    valid_until: datetime
    upper_cost: Money | None = None

    @model_validator(mode="after")
    def _aware(self) -> "MandateCostQuote":
        if self.valid_until.utcoffset() is None:
            raise ValueError("quote expiry must include timezone")
        return self


class MandateReservationRequest(MandateContract):
    logical_effect_key: CanonicalKey
    input_fingerprint: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    quote: MandateCostQuote
    model_route: MandateModelRoute | None = None
    model_price_binding: ModelPriceBinding | None = None
    capability_grant: MandateCapabilityGrant | None = None
    external_grant: MandateExternalGrant | None = None
    internal_write_units: int = Field(default=0, ge=0, le=10000)
    external_effect_units: int = Field(default=0, ge=0, le=10000)

    @model_validator(mode="after")
    def _one_operation(self) -> "MandateReservationRequest":
        if bool(self.model_route) == bool(self.capability_grant):
            raise ValueError("reserve exactly one model request or tool call")
        external = bool(
            self.capability_grant
            and self.capability_grant.operation == "external_effect"
        )
        if external != bool(self.external_grant):
            raise ValueError("external effects need exact destination attribution")
        writes = bool(
            self.capability_grant
            and self.capability_grant.operation == "internal_write"
        )
        if writes != bool(self.internal_write_units) or external != bool(
            self.external_effect_units
        ):
            raise ValueError("host-derived effect volumes must match operation class")
        if self.model_price_binding is not None:
            binding = self.model_price_binding
            if (
                self.model_route != binding.request.route
                or self.input_fingerprint != binding.request.input_fingerprint
                or self.quote.model_dump() != binding.quote_fields()
            ):
                raise ValueError(
                    "model reservation must retain its exact price binding"
                )
        return self

    def fingerprint(self) -> str:
        payload = self.model_dump(mode="json")
        if self.model_price_binding is None:
            # Preserve historical unbound reservation fingerprints.
            payload.pop("model_price_binding", None)
        else:
            payload["model_price_binding"] = (
                self.model_price_binding.canonical_payload()
            )
        payload["quote"]["upper_cost"] = (
            money_units(self.quote.upper_cost)
            if self.quote.upper_cost is not None
            else None
        )
        from datetime import timezone

        payload["quote"]["valid_until"] = self.quote.valid_until.astimezone(
            timezone.utc
        ).isoformat()
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
