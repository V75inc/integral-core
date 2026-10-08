"""Physical SDK admission ordering; synthetic policies are not live billing."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.agentive.harness.contracts import HarnessExecutionScope, ResolvedModelRoute
from app.agentive.harness.litellm_model import LiteLLMSDKTransport
from app.agentive.services import work_model_admission as admission
from app.agentive.services.work_execution import effect_key
from app.schemas.agentive.model_dispatch import ModelPayloadBounds
from app.schemas.agentive.work import WorkError, WorkExecutionContext
from app.schemas.agentive.work_price import ModelTokenPriceEvidence
from app.services import host_hooks


@pytest.fixture(autouse=True)
def isolated_bounds_and_price():
    previous_bounds = host_hooks.get_model_bounds_resolver()
    previous_price = host_hooks.get_model_price_resolver()
    host_hooks.register_model_bounds_resolver(None)
    host_hooks.register_model_price_resolver(None)
    yield
    host_hooks.register_model_bounds_resolver(previous_bounds)
    host_hooks.register_model_price_resolver(previous_price)


@pytest.fixture
def physical_boundary(monkeypatch):
    monkeypatch.setenv("LITELLM_MODE", "PRODUCTION")
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", "/tmp/integral-litellm-test-cache")
    context = WorkExecutionContext(
        work_item_id="work-1",
        attempt=1,
        run_id="run-1",
        principal_id="user-1",
        workspace_id="workspace-1",
        thread_id="thread-1",
        logical_step_key="provider:0",
        effect_key=effect_key(work_item_id="work-1", logical_step_key="provider:0"),
        lease_token="synthetic-lease",
        lease_fence=1,
    )
    scope = HarnessExecutionScope(
        tenant_id=context.workspace_id,
        workspace_id=context.workspace_id,
        principal_id=context.principal_id,
        thread_id=context.thread_id,
        run_id=context.run_id,
        session_id="session-1",
        permission_revision="permission-1",
        capability_version="capability-1",
    )
    route = ResolvedModelRoute(
        provider="ollama_chat",
        model="ollama_chat/synthetic",
        credential_source="local",
        credential_ref="synthetic-route-generation",
        api_base="http://synthetic-provider.invalid",
        ollama_num_ctx=32768,
        ollama_num_predict=128,
        ollama_think=False,
    )
    events = []
    reservations = []
    dispatches = []

    async def reserve(**kwargs):
        events.append("reserve")
        reservations.append(kwargs)
        return SimpleNamespace(reservation_id=f"reservation-{len(reservations)}")

    async def mark(**kwargs):
        events.append("mark")
        dispatches.append(kwargs)

    async def observe(observation):
        events.append(observation.outcome)

    async def complete(**kwargs):
        events.append("sdk")
        return {
            "id": "synthetic-response",
            "choices": [],
            "usage": {
                "prompt_tokens": 2,
                "completion_tokens": 1,
                "total_tokens": 3,
            },
        }

    monkeypatch.setattr(admission, "assert_work_item_execution_current", AsyncMock())
    monkeypatch.setattr(admission, "assert_effect_boundary_allowed", AsyncMock())
    monkeypatch.setattr(admission, "_is_mandate_work", AsyncMock(return_value=True))
    monkeypatch.setattr(admission, "reserve_mandate_budget", reserve)
    monkeypatch.setattr(admission, "mark_mandate_dispatch_intent", mark)
    current_dispatch = AsyncMock()
    monkeypatch.setattr(
        admission, "assert_mandate_model_dispatch_current", current_dispatch
    )
    settlement = AsyncMock(side_effect=WorkError("work.cost_unavailable"))
    monkeypatch.setattr(admission, "settle_mandate_model_receipt", settlement)
    authority = AsyncMock()
    boundary = admission.WorkModelAdmission(
        context=context,
        assert_authority=authority,
        observer=observe,
    )
    transport = LiteLLMSDKTransport(
        route=route,
        scope=scope,
        observer=boundary.observe,
        admission=boundary,
        completion=complete,
    )
    payloads = []

    async def bounds(dispatch):
        payloads.append(dispatch)
        events.append("bounds")
        return ModelPayloadBounds(
            input_fingerprint=dispatch.fingerprint(),
            input_tokens_upper=32768,
            output_tokens_upper=128,
            bounds_ref="synthetic-bound-policy",
            valid_until=datetime.now(timezone.utc) + timedelta(minutes=1),
        )

    async def price(request):
        events.append("price")
        return ModelTokenPriceEvidence(
            request_digest=request.digest(),
            route=request.route,
            policy_ref="synthetic-price-policy",
            source_ref="synthetic-source",
            account_terms_ref="synthetic-account",
            valid_from=datetime.now(timezone.utc),
            valid_until=datetime.now(timezone.utc) + timedelta(minutes=1),
            input_usd_per_million=Decimal("0.30"),
            output_usd_per_million=Decimal("1.20"),
            request_fee_upper_usd=Decimal("0"),
        )

    return SimpleNamespace(**locals())


async def call(boundary):
    """Submit a final OpenAI request directly to the physical bridge."""
    return await boundary.transport.handle_async_request(
        httpx.Request(
            "POST",
            "http://bridge.invalid/v1/chat/completions",
            json={
                "model": boundary.route.model,
                "messages": [{"role": "user", "content": "Hi"}],
                "tools": [
                    {"type": "function", "function": {"name": "read", "parameters": {}}}
                ],
                "max_completion_tokens": 10000,
                "api_key": "untrusted-secret",
                "api_base": "http://untrusted.invalid",
            },
        )
    )


@pytest.mark.asyncio
async def test_no_bounds_prevents_sdk_intent_and_reservation(physical_boundary):
    b = physical_boundary
    with pytest.raises(WorkError) as exc:
        await call(b)
    assert exc.value.code == "work.model_bounds_unavailable"
    assert b.events == []


@pytest.mark.asyncio
async def test_missing_price_never_dispatches(physical_boundary):
    b = physical_boundary
    host_hooks.register_model_bounds_resolver(b.bounds)
    with pytest.raises(WorkError) as exc:
        await call(b)
    assert exc.value.code == "work.price_resolver_unavailable"
    assert b.events == ["bounds"]


@pytest.mark.asyncio
async def test_actual_mapped_payload_and_price_precede_sdk(physical_boundary):
    b = physical_boundary
    host_hooks.register_model_bounds_resolver(b.bounds)
    host_hooks.register_model_price_resolver(b.price)
    assert (await call(b)).status_code == 200
    assert b.events == [
        "bounds",
        "price",
        "reserve",
        "mark",
        "dispatch_intent",
        "sdk",
        "responded",
    ]
    dispatch = b.payloads[0]
    payload = json.loads(dispatch.sdk_payload_json)
    assert payload["max_tokens"] == 128
    assert "max_completion_tokens" not in payload
    assert payload["extra_body"] == {"options": {"num_ctx": 32768}, "think": False}
    assert payload["tools"][0]["function"]["name"] == "read"
    assert payload["num_retries"] == 0
    assert "api_key" not in payload and "api_base" not in payload
    assert "untrusted-secret" not in repr(dispatch)
    assert b.reservations[0]["request"].input_fingerprint == dispatch.fingerprint()
    assert b.dispatches[0]["dispatch_ref"] == dispatch.request_id
    assert b.authority.await_count == 4
    assert b.current_dispatch.await_count == 1
    assert b.settlement.await_count == 1


@pytest.mark.asyncio
async def test_failed_ledger_gate_prevents_sdk(physical_boundary, monkeypatch):
    b = physical_boundary
    host_hooks.register_model_bounds_resolver(b.bounds)
    host_hooks.register_model_price_resolver(b.price)
    monkeypatch.setattr(
        admission,
        "reserve_mandate_budget",
        AsyncMock(side_effect=WorkError("work.budget_exhausted")),
    )
    with pytest.raises(WorkError) as exc:
        await call(b)
    assert exc.value.code == "work.budget_exhausted"
    assert b.events == ["bounds", "price"]


@pytest.mark.asyncio
async def test_final_route_revocation_prevents_mark_and_sdk(physical_boundary):
    b = physical_boundary
    host_hooks.register_model_bounds_resolver(b.bounds)
    host_hooks.register_model_price_resolver(b.price)
    b.authority.side_effect = [None, None, WorkError("work.model_route_stale")]
    with pytest.raises(WorkError):
        await call(b)
    assert b.events == ["bounds", "price", "reserve"]


@pytest.mark.asyncio
async def test_each_physical_request_has_own_logical_slot(physical_boundary):
    b = physical_boundary
    host_hooks.register_model_bounds_resolver(b.bounds)
    host_hooks.register_model_price_resolver(b.price)
    await asyncio.gather(call(b), call(b))
    assert len({x.request_id for x in b.payloads}) == 2
    assert [x["execution_context"].logical_step_key for x in b.reservations] == [
        "provider:0",
        "provider:1",
    ]
    assert len({x["request"].logical_effect_key for x in b.reservations}) == 2


@pytest.mark.asyncio
async def test_ordinary_chat_work_does_not_require_mandate(
    physical_boundary, monkeypatch
):
    b = physical_boundary
    monkeypatch.setattr(admission, "_is_mandate_work", AsyncMock(return_value=False))
    assert (await call(b)).status_code == 200
    assert b.events == ["dispatch_intent", "sdk", "responded"]
    assert b.settlement.await_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["changed", "expired", "exception"])
async def test_bad_host_bounds_are_not_dispatch_proof(physical_boundary, failure):
    b = physical_boundary

    async def bad(dispatch):
        if failure == "exception":
            raise RuntimeError("secret host error")
        result = await b.bounds(dispatch)
        return result.model_copy(
            update={
                "input_fingerprint": (
                    "a" * 64 if failure == "changed" else dispatch.fingerprint()
                ),
                "valid_until": (
                    datetime.now(timezone.utc) - timedelta(seconds=1)
                    if failure == "expired"
                    else result.valid_until
                ),
            }
        )

    host_hooks.register_model_bounds_resolver(bad)
    with pytest.raises(WorkError) as exc:
        await call(b)
    assert "secret host error" not in str(exc.value)
    assert "sdk" not in b.events and "reserve" not in b.events


@pytest.mark.asyncio
async def test_host_cancellation_does_not_become_a_fallback(physical_boundary):
    b = physical_boundary
    host_hooks.register_model_bounds_resolver(
        AsyncMock(side_effect=asyncio.CancelledError())
    )
    with pytest.raises(asyncio.CancelledError):
        await call(b)
    assert b.events == []


@pytest.mark.asyncio
async def test_physical_lease_failure_prevents_even_host_resolution(
    physical_boundary, monkeypatch
):
    b = physical_boundary
    monkeypatch.setattr(
        admission,
        "assert_work_item_execution_current",
        AsyncMock(side_effect=WorkError("work.lease_lost")),
    )
    with pytest.raises(WorkError):
        await call(b)
    assert b.events == []


@pytest.mark.asyncio
async def test_failed_intent_marker_cannot_call_sdk(physical_boundary, monkeypatch):
    b = physical_boundary
    host_hooks.register_model_bounds_resolver(b.bounds)
    host_hooks.register_model_price_resolver(b.price)
    monkeypatch.setattr(
        admission,
        "mark_mandate_dispatch_intent",
        AsyncMock(side_effect=WorkError("work.lease_lost")),
    )
    with pytest.raises(WorkError):
        await call(b)
    assert b.events == ["bounds", "price", "reserve"]


@pytest.mark.asyncio
async def test_physical_id_cannot_be_reused(physical_boundary):
    b = physical_boundary
    host_hooks.register_model_bounds_resolver(b.bounds)
    host_hooks.register_model_price_resolver(b.price)
    await call(b)
    baseline = list(b.events)
    with pytest.raises(WorkError) as exc:
        await b.boundary(b.payloads[0])
    assert exc.value.code == "work.idempotency_conflict"
    assert b.events == baseline


@pytest.mark.asyncio
async def test_foreign_tenant_does_not_enter_price_policy(physical_boundary):
    b = physical_boundary
    host_hooks.register_model_bounds_resolver(b.bounds)
    host_hooks.register_model_price_resolver(b.price)
    await call(b)
    baseline = list(b.events)
    dispatch = b.payloads[0].model_copy(
        update={"scope": b.scope.model_copy(update={"tenant_id": "foreign"})}
    )
    with pytest.raises(WorkError) as exc:
        await b.boundary(dispatch)
    assert exc.value.code == "work.mandate_scope_denied"
    assert b.events == baseline


@pytest.mark.asyncio
async def test_receipt_delay_can_expire_bounds_before_sdk(
    physical_boundary, monkeypatch
):
    b = physical_boundary
    host_hooks.register_model_bounds_resolver(b.bounds)
    host_hooks.register_model_price_resolver(b.price)

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(minutes=2)

    async def delayed(observation):
        b.events.append(observation.outcome)
        if observation.outcome == "dispatch_intent":
            monkeypatch.setattr(admission, "datetime", Later)

    b.boundary._observer = delayed
    with pytest.raises(WorkError) as exc:
        await call(b)
    assert exc.value.code == "work.model_bounds_expired"
    assert b.events == ["bounds", "price", "reserve", "mark", "dispatch_intent"]
    assert b.current_dispatch.await_count == 1


@pytest.mark.asyncio
async def test_receipt_delay_can_expire_price_before_sdk(
    physical_boundary, monkeypatch
):
    b = physical_boundary
    host_hooks.register_model_bounds_resolver(b.bounds)

    async def short_price(request):
        result = await b.price(request)
        return result.model_copy(
            update={"valid_until": datetime.now(timezone.utc) + timedelta(seconds=1)}
        )

    host_hooks.register_model_price_resolver(short_price)

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(seconds=2)

    async def delayed(observation):
        b.events.append(observation.outcome)
        if observation.outcome == "dispatch_intent":
            monkeypatch.setattr(admission, "datetime", Later)

    b.boundary._observer = delayed
    with pytest.raises(WorkError) as exc:
        await call(b)
    assert exc.value.code == "work.cost_quote_expired"
    assert "sdk" not in b.events


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["work.permission_stale", "work.model_route_stale"])
async def test_authority_revocation_during_receipt_prevents_sdk(
    physical_boundary, code
):
    b = physical_boundary
    host_hooks.register_model_bounds_resolver(b.bounds)
    host_hooks.register_model_price_resolver(b.price)
    b.authority.side_effect = [None, None, None, WorkError(code)]
    with pytest.raises(WorkError) as exc:
        await call(b)
    assert exc.value.code == code
    assert b.events[-1] == "dispatch_intent"
    assert "sdk" not in b.events


@pytest.mark.asyncio
async def test_current_ledger_revocation_after_receipt_prevents_sdk(physical_boundary):
    b = physical_boundary
    host_hooks.register_model_bounds_resolver(b.bounds)
    host_hooks.register_model_price_resolver(b.price)
    b.current_dispatch.side_effect = WorkError("work.mandate_inactive")
    with pytest.raises(WorkError):
        await call(b)
    assert "dispatch_intent" in b.events and "sdk" not in b.events


@pytest.mark.asyncio
async def test_readiness_is_consumed_even_if_it_fails():
    check = AsyncMock(side_effect=WorkError("work.cancelled"))
    ready = admission.WorkModelAdmission._one_shot(check)
    with pytest.raises(WorkError):
        await ready()
    with pytest.raises(WorkError) as exc:
        await ready()
    assert exc.value.code == "work.outcome_reconciliation_required"
    assert check.await_count == 1


@pytest.mark.asyncio
async def test_missing_readiness_is_not_a_silent_fallback(physical_boundary):
    b = physical_boundary
    b.transport._admission = AsyncMock(return_value=None)
    with pytest.raises(RuntimeError, match="final dispatch readiness"):
        await call(b)
    assert b.events == []


@pytest.mark.asyncio
async def test_ordinary_chat_lease_revoked_during_receipt_prevents_sdk(
    physical_boundary, monkeypatch
):
    b = physical_boundary
    monkeypatch.setattr(admission, "_is_mandate_work", AsyncMock(return_value=False))
    current = AsyncMock(side_effect=[None, WorkError("work.lease_lost")])
    monkeypatch.setattr(admission, "assert_work_item_execution_current", current)
    with pytest.raises(WorkError):
        await call(b)
    assert b.events == ["dispatch_intent"]
