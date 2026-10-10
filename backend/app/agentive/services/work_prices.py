"""Resolve conservative model admission prices from registered trusted host evidence."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from pydantic import ValidationError

from app.schemas.agentive.work import WorkError
from app.schemas.agentive.work_budget import MandateCostQuote, MandateReservationRequest
from app.schemas.agentive.work_price import (
    ModelPriceBinding,
    ModelPriceRequest,
    ModelTokenPriceEvidence,
)
from app.services.host_hooks import get_model_price_resolver

_PRICE_RESOLUTION_TIMEOUT_SECONDS = 10.0


async def _resolve_model_price_binding(
    request: ModelPriceRequest,
) -> ModelPriceBinding:
    """Never infer price from a route name, chat, LiteLLM estimate or missing hook.

    The physical host must establish payload bounds before this call. The
    registered resolver must verify their applicability to the selected route
    and account terms. No reservation, permission or dispatch is granted here.
    """
    try:
        request = ModelPriceRequest.model_validate(request.model_dump(mode="python"))
    except (ValidationError, AttributeError) as exc:
        raise WorkError("work.price_request_invalid") from exc
    if request.bounds_valid_until <= datetime.now(timezone.utc):
        raise WorkError("work.model_bounds_expired")
    resolver = get_model_price_resolver()
    if resolver is None:
        raise WorkError("work.price_resolver_unavailable")
    try:
        candidate = await asyncio.wait_for(
            resolver(request), timeout=_PRICE_RESOLUTION_TIMEOUT_SECONDS
        )
        evidence = ModelTokenPriceEvidence.model_validate(
            candidate.model_dump(mode="python")
        )
    except WorkError:
        raise
    except Exception as exc:
        # Do not surface host exceptions that may contain account/credential data.
        raise WorkError("work.price_resolution_failed") from exc
    now = datetime.now(timezone.utc)
    if request.bounds_valid_until <= now:
        raise WorkError("work.model_bounds_expired")
    if evidence.route != request.route or evidence.request_digest != request.digest():
        raise WorkError("work.price_evidence_invalid")
    if not evidence.valid_from <= now < evidence.valid_until:
        raise WorkError("work.cost_quote_expired")
    return ModelPriceBinding(request=request, evidence=evidence)


async def resolve_mandate_model_cost_quote(
    request: ModelPriceRequest,
) -> MandateCostQuote:
    """Compute a host-attested ceiling without granting dispatch authority."""
    binding = await _resolve_model_price_binding(request)
    try:
        return MandateCostQuote(**binding.quote_fields())
    except ValidationError as exc:
        raise WorkError("work.price_evidence_invalid") from exc


async def resolve_mandate_model_reservation_request(
    request: ModelPriceRequest,
    *,
    logical_effect_key: str,
) -> MandateReservationRequest:
    """Retain bounds and evidence in the durable reservation input.

    This creates no reservation or authority. The physical adapter must derive
    actual payload bounds, then use the existing shared-budget and lease gates.
    """
    binding = await _resolve_model_price_binding(request)
    try:
        return MandateReservationRequest(
            logical_effect_key=logical_effect_key,
            input_fingerprint=binding.request.input_fingerprint,
            quote=MandateCostQuote(**binding.quote_fields()),
            model_route=binding.request.route,
            model_price_binding=binding,
        )
    except ValidationError as exc:
        raise WorkError("work.price_evidence_invalid") from exc
