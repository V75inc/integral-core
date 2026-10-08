"""Host admission quotes; synthetic tariffs are not live provider billing proof."""

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from unittest.mock import AsyncMock

import pytest

from app.agentive.harness.contracts import HarnessExecutionScope
from app.agentive.services import work_prices
from app.schemas.agentive.work import WorkError
from app.schemas.agentive.work_mandate import MandateModelRoute
from app.schemas.agentive.work_price import ModelPriceRequest, ModelTokenPriceEvidence
from app.services import host_hooks


@pytest.fixture(autouse=True)
def isolated_host_hook():
    previous = host_hooks.get_model_price_resolver()
    host_hooks.register_model_price_resolver(None)
    yield
    host_hooks.register_model_price_resolver(previous)


@pytest.fixture
def price_request():
    return ModelPriceRequest(
        scope=HarnessExecutionScope(
            tenant_id="workspace-1",
            workspace_id="workspace-1",
            principal_id="user-1",
            thread_id="thread-1",
            session_id="session-1",
            run_id="run-1",
            permission_revision="permission-v1",
            capability_version="capability-v1",
        ),
        route=MandateModelRoute(
            provider="provider-1",
            model="model-1",
            credential_source="platform",
            credential_ref="generation-v1",
        ),
        request_id="price_request-1",
        input_fingerprint="a" * 64,
        input_tokens_upper=32768,
        output_tokens_upper=8192,
        bounds_ref="synthetic-verified-bounds-v1",
        bounds_valid_until=datetime.now(timezone.utc) + timedelta(minutes=5),
    )


def evidence(price_request, **updates):
    values = dict(
        request_digest=price_request.digest(),
        policy_ref="synthetic-policy-v1",
        source_ref="synthetic-price-source-v1",
        account_terms_ref="synthetic-account-terms-v1",
        route=price_request.route,
        valid_from=datetime.now(timezone.utc) - timedelta(minutes=1),
        valid_until=datetime.now(timezone.utc) + timedelta(minutes=10),
        input_usd_per_million=Decimal("0.30"),
        output_usd_per_million=Decimal("1.20"),
        request_fee_upper_usd=Decimal("0"),
    )
    values.update(updates)
    return ModelTokenPriceEvidence(**values)


@pytest.mark.asyncio
async def test_missing_host_price_resolver_is_not_a_free_quote(price_request):
    with pytest.raises(WorkError) as exc:
        await work_prices.resolve_mandate_model_cost_quote(price_request)
    assert exc.value.code == "work.price_resolver_unavailable"


@pytest.mark.asyncio
async def test_host_rates_and_fee_produce_exact_bounded_ceiling(price_request):
    handler = AsyncMock(
        return_value=evidence(price_request, request_fee_upper_usd=Decimal("0.01"))
    )
    host_hooks.register_model_price_resolver(handler)
    quote = await work_prices.resolve_mandate_model_cost_quote(price_request)
    handler.assert_awaited_once_with(price_request)
    assert quote.upper_cost == Decimal("0.02966080")
    assert quote.credential_ref == price_request.route.credential_ref
    assert quote.provider == price_request.route.provider
    assert quote.model == price_request.route.model
    assert quote.valid_until == price_request.bounds_valid_until


@pytest.mark.asyncio
async def test_quote_rounds_up_under_low_ambient_decimal_precision(price_request):
    price_request = price_request.model_copy(
        update={"input_tokens_upper": 1, "output_tokens_upper": 1}
    )
    host_hooks.register_model_price_resolver(
        AsyncMock(
            return_value=evidence(
                price_request,
                input_usd_per_million=Decimal("0.00000001"),
                output_usd_per_million=Decimal("0"),
            )
        )
    )
    with localcontext() as context:
        context.prec = 2
        quote = await work_prices.resolve_mandate_model_cost_quote(price_request)
    assert quote.upper_cost == Decimal("0.00000001")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    [
        "principal_id",
        "thread_id",
        "session_id",
        "run_id",
        "permission_revision",
        "capability_version",
    ],
)
async def test_price_evidence_cannot_be_replayed_into_another_execution(
    price_request, field
):
    host_hooks.register_model_price_resolver(
        AsyncMock(return_value=evidence(price_request))
    )
    changed = price_request.model_copy(
        update={"scope": price_request.scope.model_copy(update={field: "other"})}
    )
    with pytest.raises(WorkError) as exc:
        await work_prices.resolve_mandate_model_cost_quote(changed)
    assert exc.value.code == "work.price_evidence_invalid"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field", ["provider", "model", "credential_source", "credential_ref"]
)
async def test_exact_route_is_required(price_request, field):
    host_hooks.register_model_price_resolver(
        AsyncMock(return_value=evidence(price_request))
    )
    changed = price_request.model_copy(
        update={
            "route": price_request.route.model_copy(
                update={field: "local" if field == "credential_source" else "other"}
            )
        }
    )
    with pytest.raises(WorkError) as exc:
        await work_prices.resolve_mandate_model_cost_quote(changed)
    assert exc.value.code == "work.price_evidence_invalid"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("request_id", "other"),
        ("input_fingerprint", "b" * 64),
        ("input_tokens_upper", 32769),
        ("output_tokens_upper", 8193),
        ("bounds_ref", "other"),
    ],
)
async def test_operation_or_bounds_change_requires_new_price_evidence(
    price_request, field, value
):
    host_hooks.register_model_price_resolver(
        AsyncMock(return_value=evidence(price_request))
    )
    with pytest.raises(WorkError) as exc:
        await work_prices.resolve_mandate_model_cost_quote(
            price_request.model_copy(update={field: value})
        )
    assert exc.value.code == "work.price_evidence_invalid"


@pytest.mark.asyncio
async def test_expired_bounds_skip_price_handler(price_request):
    handler = AsyncMock(return_value=evidence(price_request))
    host_hooks.register_model_price_resolver(handler)
    price_request = price_request.model_copy(
        update={"bounds_valid_until": datetime.now(timezone.utc) - timedelta(seconds=1)}
    )
    with pytest.raises(WorkError) as exc:
        await work_prices.resolve_mandate_model_cost_quote(price_request)
    assert exc.value.code == "work.model_bounds_expired"
    handler.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("future", [False, True])
async def test_tariff_must_be_current(price_request, future):
    now = datetime.now(timezone.utc)
    value = evidence(
        price_request,
        valid_from=now + timedelta(minutes=1) if future else now - timedelta(minutes=2),
        valid_until=(
            now + timedelta(minutes=2) if future else now - timedelta(minutes=1)
        ),
    )
    host_hooks.register_model_price_resolver(AsyncMock(return_value=value))
    with pytest.raises(WorkError) as exc:
        await work_prices.resolve_mandate_model_cost_quote(price_request)
    assert exc.value.code == "work.cost_quote_expired"


@pytest.mark.asyncio
async def test_host_error_is_sanitized_without_fallback(price_request):
    host_hooks.register_model_price_resolver(
        AsyncMock(side_effect=RuntimeError("synthetic-private-account-detail"))
    )
    with pytest.raises(WorkError) as exc:
        await work_prices.resolve_mandate_model_cost_quote(price_request)
    assert exc.value.code == "work.price_resolution_failed"
    assert "synthetic-private" not in str(exc.value)


@pytest.mark.asyncio
async def test_equivalent_decimal_and_timezone_forms_keep_quote_identity(price_request):
    value = evidence(price_request)
    host_hooks.register_model_price_resolver(AsyncMock(return_value=value))
    first = await work_prices.resolve_mandate_model_cost_quote(price_request)
    equivalent = value.model_copy(
        update={
            "input_usd_per_million": Decimal("0.30000000"),
            "valid_from": value.valid_from.astimezone(timezone(timedelta(hours=-4))),
            "valid_until": value.valid_until.astimezone(timezone(timedelta(hours=-4))),
        }
    )
    host_hooks.register_model_price_resolver(AsyncMock(return_value=equivalent))
    second = await work_prices.resolve_mandate_model_cost_quote(price_request)
    assert first == second


@pytest.mark.parametrize(
    "field",
    ["input_usd_per_million", "output_usd_per_million", "request_fee_upper_usd"],
)
def test_negative_and_missing_prices_are_rejected(price_request, field):
    from pydantic import ValidationError

    value = evidence(price_request).model_dump()
    value.pop(field)
    with pytest.raises(ValidationError):
        ModelTokenPriceEvidence(**value)
    value[field] = Decimal("-1")
    with pytest.raises(ValidationError):
        ModelTokenPriceEvidence(**value)


@pytest.mark.asyncio
async def test_stalled_host_resolver_fails_closed(price_request, monkeypatch):
    async def stalled(_):
        await asyncio.Event().wait()

    host_hooks.register_model_price_resolver(stalled)
    monkeypatch.setattr(work_prices, "_PRICE_RESOLUTION_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(WorkError) as exc:
        await work_prices.resolve_mandate_model_cost_quote(price_request)
    assert exc.value.code == "work.price_resolution_failed"


@pytest.mark.asyncio
async def test_bounds_expiring_during_resolution_are_rejected(price_request):
    price_request = price_request.model_copy(
        update={
            "bounds_valid_until": datetime.now(timezone.utc)
            + timedelta(milliseconds=20)
        }
    )
    value = evidence(price_request)

    async def delayed(_):
        await asyncio.sleep(0.04)
        return value

    host_hooks.register_model_price_resolver(delayed)
    with pytest.raises(WorkError) as exc:
        await work_prices.resolve_mandate_model_cost_quote(price_request)
    assert exc.value.code == "work.model_bounds_expired"


@pytest.mark.asyncio
async def test_wrong_host_result_shape_is_sanitized(price_request):
    host_hooks.register_model_price_resolver(
        AsyncMock(return_value={"secret": "private"})
    )
    with pytest.raises(WorkError) as exc:
        await work_prices.resolve_mandate_model_cost_quote(price_request)
    assert exc.value.code == "work.price_resolution_failed"
    assert "private" not in str(exc.value)


@pytest.mark.asyncio
async def test_explicit_zero_requires_complete_host_evidence(price_request):
    host_hooks.register_model_price_resolver(
        AsyncMock(
            return_value=evidence(
                price_request,
                input_usd_per_million=Decimal("0"),
                output_usd_per_million=Decimal("0"),
                request_fee_upper_usd=Decimal("0"),
            )
        )
    )
    quote = await work_prices.resolve_mandate_model_cost_quote(price_request)
    assert quote.upper_cost == Decimal("0")
    assert quote.quote_ref.startswith("host-model-price:")


@pytest.mark.parametrize(
    "value", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")]
)
def test_nonfinite_prices_are_rejected(price_request, value):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        evidence(price_request, input_usd_per_million=value)


@pytest.mark.asyncio
async def test_unchecked_invalid_bounds_are_revalidated(price_request):
    handler = AsyncMock(return_value=evidence(price_request))
    host_hooks.register_model_price_resolver(handler)
    with pytest.raises(WorkError) as exc:
        await work_prices.resolve_mandate_model_cost_quote(
            price_request.model_copy(update={"input_tokens_upper": True})
        )
    assert exc.value.code == "work.price_request_invalid"
    handler.assert_not_awaited()
