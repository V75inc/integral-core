"""Real shared ledger and encrypted receipts around a mocked physical SDK.

Synthetic approved lineage and tariffs; no public approval or live billing proof.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from functools import partial
from unittest.mock import AsyncMock

import httpx
import pytest

from app.agentive.harness.contracts import ResolvedModelRoute
from app.agentive.harness.litellm_model import LiteLLMSDKTransport
from app.agentive.harness.model_observations import persist_model_request_observation
from app.agentive.services.work_model_admission import WorkModelAdmission
from app.schemas.agentive.model_dispatch import ModelPayloadBounds
from app.schemas.agentive.work import WorkError
from app.schemas.agentive.work_price import ModelTokenPriceEvidence
from app.services import host_hooks
from tests.contract.test_work_budgets_postgres import (
    budget_root,
    leased_budget_child,
    ledger,
)
from tests.contract.test_work_model_receipts_postgres import scope_for

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def isolated_host_bounds_prices(monkeypatch):
    monkeypatch.setenv("LITELLM_MODE", "PRODUCTION")
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", "/tmp/integral-litellm-test-cache")
    previous_bounds = host_hooks.get_model_bounds_resolver()
    previous_price = host_hooks.get_model_price_resolver()
    yield
    host_hooks.register_model_bounds_resolver(previous_bounds)
    host_hooks.register_model_price_resolver(previous_price)


@pytest.mark.parametrize("reported_cost", [None, "0.0001"])
async def test_sdk_follows_real_hold_and_intent_and_cannot_exceed_count(
    leased_budget_child,
    reported_cost,
):
    root, parent, leaf, context, original, db = leased_budget_child
    route = ResolvedModelRoute(
        **original.model_route.model_dump(),
        api_key="synthetic-credential",
    )
    scope = scope_for(context)
    bounds_seen = []
    calls = []

    async def bounds(dispatch):
        bounds_seen.append(dispatch)
        return ModelPayloadBounds(
            input_fingerprint=dispatch.fingerprint(),
            input_tokens_upper=1000,
            output_tokens_upper=1000,
            bounds_ref="synthetic-bounds-v1",
            valid_until=datetime.now(timezone.utc) + timedelta(minutes=1),
        )

    async def price(request):
        return ModelTokenPriceEvidence(
            request_digest=request.digest(),
            route=request.route,
            policy_ref="synthetic-price-policy",
            source_ref="synthetic-price-source",
            account_terms_ref="synthetic-account-terms",
            valid_from=datetime.now(timezone.utc) - timedelta(seconds=1),
            valid_until=datetime.now(timezone.utc) + timedelta(minutes=1),
            input_usd_per_million=Decimal("0.30"),
            output_usd_per_million=Decimal("1.20"),
            request_fee_upper_usd=Decimal("0"),
        )

    host_hooks.register_model_bounds_resolver(bounds)
    host_hooks.register_model_price_resolver(price)

    async def sdk(**kwargs):
        # Inspect committed database facts before the mocked provider runs.
        dispatch = bounds_seen[-1]
        records = await db.find(
            "object",
            {
                "context.root_work_item_id": root.work_item_id,
                "context.dispatch_ref": dispatch.request_id,
            },
        )
        assert len(records) == 1
        record = records[0]["context"]
        assert record["dispatch_state"] == "intent"
        assert record["model_dispatch_scope"] == scope.model_dump(mode="json")
        assert record["request"]["input_fingerprint"] == dispatch.fingerprint()
        assert (
            record["request"]["model_price_binding"]["request"]["request_id"]
            == dispatch.request_id
        )
        calls.append(dispatch.request_id)
        usage = {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}
        if reported_cost is not None:
            usage["cost"] = reported_cost
        return {"id": f"synthetic-provider-{len(calls)}", "choices": [], "usage": usage}

    def adapter():
        boundary = WorkModelAdmission(
            context=context,
            assert_authority=AsyncMock(),
            observer=partial(
                persist_model_request_observation, work_execution_context=context
            ),
        )
        return LiteLLMSDKTransport(
            route=route,
            scope=scope,
            observer=boundary.observe,
            admission=boundary,
            completion=sdk,
        )

    transport = adapter()

    async def send(target):
        return await target.handle_async_request(
            httpx.Request(
                "POST",
                "http://synthetic-bridge.invalid/v1/chat/completions",
                json={
                    "model": route.model,
                    "messages": [{"role": "user", "content": "Private summary"}],
                    "max_tokens": 1000,
                },
            )
        )

    for _ in range(3):
        assert (await send(transport)).status_code == 200
    baseline = await ledger(db, root)
    assert baseline["model_requests"] == 3
    assert baseline["reserved_units"] == (450000 if reported_cost is None else 0)
    assert baseline["charged_units"] == (0 if reported_cost is None else 30000)
    with pytest.raises(WorkError) as exc:
        await send(transport)
    assert exc.value.code == "work.budget_exhausted"
    assert len(calls) == 3
    assert await ledger(db, root) == baseline
    # Reconstructed run adapter cannot reuse ordinal zero after dispatch.
    with pytest.raises(WorkError) as exc:
        await send(adapter())
    assert exc.value.code in {
        "work.idempotency_conflict",
        "work.outcome_reconciliation_required",
    }
    assert len(calls) == 3
    assert await ledger(db, root) == baseline
    stored = await db.find("object", {"context.root_work_item_id": root.work_item_id})
    reservations = [
        row for row in stored if row["id"].startswith("o.WorkBudgetReservation.")
    ]
    assert len(reservations) == 3
    for row in reservations:
        assert row["context"]["status"] == (
            "reserved" if reported_cost is None else "settled"
        )
