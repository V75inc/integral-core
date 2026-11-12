"""Opt-in operator attestations for bounded, text-only model requests."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import Field, TypeAdapter, model_validator

from app.schemas.agentive.model_dispatch import ModelDispatchInput, ModelPayloadBounds
from app.schemas.agentive.work import WorkError
from app.schemas.agentive.work_mandate import (
    CanonicalKey,
    MandateContract,
    MandateModelRoute,
)
from app.schemas.agentive.work_money import Money
from app.schemas.agentive.work_price import ModelPriceRequest, ModelTokenPriceEvidence

_OUTPUT_LIMITS = {"max_tokens", "max_completion_tokens"}
_ALLOWED_KEYS = {
    "model",
    "messages",
    "num_retries",
    "timeout",
    "stream",
} | _OUTPUT_LIMITS


class TextModelAttestation(MandateContract):
    """Host-attested ceilings for one route and a bounded text request envelope."""

    workspace_id: CanonicalKey
    route: MandateModelRoute
    endpoint_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    policy_ref: CanonicalKey
    source_ref: CanonicalKey
    account_terms_ref: CanonicalKey
    valid_from: datetime
    valid_until: datetime
    max_payload_bytes: int = Field(ge=1, le=1_000_000, strict=True)
    max_output_parameter: int = Field(ge=1, le=2_000_000, strict=True)
    input_tokens_upper: int = Field(ge=1, le=2_000_000, strict=True)
    output_tokens_upper: int = Field(ge=1, le=2_000_000, strict=True)
    input_usd_per_million: Money
    output_usd_per_million: Money
    request_fee_upper_usd: Money

    @model_validator(mode="after")
    def _validate_attestation(self) -> TextModelAttestation:
        if (
            self.valid_from.utcoffset() is None
            or self.valid_until.utcoffset() is None
            or self.valid_until <= self.valid_from
        ):
            raise ValueError("attestation requires an aware validity window")
        if self.max_output_parameter > self.output_tokens_upper:
            raise ValueError("output parameter exceeds the attested output ceiling")
        return self

    def bounds_ref(self) -> str:
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True)
        return "configured-text-v1:" + hashlib.sha256(canonical.encode()).hexdigest()


class ConfiguredModelEvidence:
    """Verify exact request shape and apply declared ceilings without estimating tokens."""

    def __init__(self, attestations: tuple[TextModelAttestation, ...]) -> None:
        self._attestations = {
            (item.workspace_id, item.route): item for item in attestations
        }
        if not attestations or len(self._attestations) != len(attestations):
            raise ValueError("model attestations must identify unique workspace routes")

    def _current(
        self, workspace_id: str, route: MandateModelRoute
    ) -> TextModelAttestation:
        item = self._attestations.get((workspace_id, route))
        now = datetime.now(timezone.utc)
        if item is None or not item.valid_from <= now < item.valid_until:
            raise WorkError("work.model_evidence_unavailable")
        return item

    async def bounds(self, dispatch: ModelDispatchInput) -> ModelPayloadBounds:
        route = MandateModelRoute.model_validate(dispatch.route.model_dump())
        item = self._current(dispatch.scope.workspace_id, route)
        if (
            dispatch.scope.tenant_id != item.workspace_id
            or dispatch.endpoint_fingerprint != item.endpoint_fingerprint
            or len(dispatch.sdk_payload_json.encode()) > item.max_payload_bytes
        ):
            raise WorkError("work.model_bounds_invalid")
        try:
            payload = json.loads(dispatch.sdk_payload_json)
        except ValueError as exc:
            raise WorkError("work.model_bounds_invalid") from exc
        if (
            not isinstance(payload, dict)
            or set(payload) - _ALLOWED_KEYS
            or payload.get("model") != route.model
            or payload.get("num_retries") != 0
            or payload.get("stream", False) is not False
        ):
            raise WorkError("work.model_bounds_invalid")
        output_keys = _OUTPUT_LIMITS & payload.keys()
        output = payload.get(next(iter(output_keys))) if len(output_keys) == 1 else None
        messages = payload.get("messages")
        if (
            type(output) is not int
            or not 1 <= output <= item.max_output_parameter
            or not isinstance(messages, list)
            or not messages
            or any(
                not isinstance(message, dict)
                or set(message) != {"role", "content"}
                or message["role"] not in ("system", "developer", "user")
                or not isinstance(message["content"], str)
                for message in messages
            )
        ):
            raise WorkError("work.model_bounds_invalid")
        return ModelPayloadBounds(
            input_fingerprint=dispatch.fingerprint(),
            input_tokens_upper=item.input_tokens_upper,
            output_tokens_upper=item.output_tokens_upper,
            bounds_ref=item.bounds_ref(),
            valid_until=min(
                item.valid_until, datetime.now(timezone.utc) + timedelta(minutes=1)
            ),
        )

    async def price(self, request: ModelPriceRequest) -> ModelTokenPriceEvidence:
        item = self._current(request.scope.workspace_id, request.route)
        if (
            request.scope.tenant_id != item.workspace_id
            or request.bounds_ref != item.bounds_ref()
            or request.input_tokens_upper != item.input_tokens_upper
            or request.output_tokens_upper != item.output_tokens_upper
            or not datetime.now(timezone.utc)
            < request.bounds_valid_until
            <= item.valid_until
        ):
            raise WorkError("work.model_bounds_invalid")
        return ModelTokenPriceEvidence(
            request_digest=request.digest(),
            policy_ref=item.policy_ref,
            source_ref=item.source_ref,
            account_terms_ref=item.account_terms_ref,
            route=item.route,
            valid_from=item.valid_from,
            valid_until=item.valid_until,
            input_usd_per_million=item.input_usd_per_million,
            output_usd_per_million=item.output_usd_per_million,
            request_fee_upper_usd=item.request_fee_upper_usd,
        )


def register_configured_model_evidence(path: str) -> None:
    """Register one explicit operator policy using the existing host hooks."""
    from app.services import host_hooks

    if host_hooks.get_model_bounds_resolver() or host_hooks.get_model_price_resolver():
        raise ValueError(
            "configured model evidence conflicts with a registered host resolver"
        )
    policies = TypeAdapter(tuple[TextModelAttestation, ...]).validate_json(
        Path(path).read_bytes()
    )
    evidence = ConfiguredModelEvidence(policies)
    host_hooks.register_model_bounds_resolver(evidence.bounds)
    host_hooks.register_model_price_resolver(evidence.price)
