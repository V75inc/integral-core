"""Resolve conservative model admission prices from registered trusted host evidence."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from decimal import ROUND_CEILING, Decimal, localcontext

from pydantic import ValidationError

from app.schemas.agentive.work import WorkError
from app.schemas.agentive.work_budget import MandateCostQuote
from app.schemas.agentive.work_price import ModelPriceRequest, ModelTokenPriceEvidence
from app.services.host_hooks import get_model_price_resolver

_PRICE_RESOLUTION_TIMEOUT_SECONDS = 10.0


async def resolve_mandate_model_cost_quote(
    request: ModelPriceRequest,
) -> MandateCostQuote:
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
    with localcontext() as context:
        context.prec = 48
        ceiling = (
            Decimal(request.input_tokens_upper) * evidence.input_usd_per_million
            + Decimal(request.output_tokens_upper) * evidence.output_usd_per_million
        ) / Decimal(1_000_000) + evidence.request_fee_upper_usd
        ceiling = ceiling.quantize(Decimal("0.00000001"), rounding=ROUND_CEILING)
    # Canonical JSON binds the exact request and host policy. Rates are normalized
    # to fixed decimal places so equivalent numeric representations do not fork IDs.
    payload = evidence.model_dump(mode="json")
    for field in (
        "input_usd_per_million",
        "output_usd_per_million",
        "request_fee_upper_usd",
    ):
        payload[field] = format(getattr(evidence, field), ".8f")
    for field in ("valid_from", "valid_until"):
        payload[field] = getattr(evidence, field).astimezone(timezone.utc).isoformat()
    identity = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    try:
        return MandateCostQuote(
            provider=request.route.provider,
            model=request.route.model,
            credential_ref=request.route.credential_ref,
            quote_ref="host-model-price:" + identity,
            valid_until=min(request.bounds_valid_until, evidence.valid_until),
            upper_cost=ceiling,
        )
    except ValidationError as exc:
        raise WorkError("work.price_evidence_invalid") from exc
