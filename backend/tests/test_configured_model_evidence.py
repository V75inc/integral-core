"""Operator evidence binds the exact native Pydantic transport request."""

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.agentive.harness.contracts import HarnessExecutionScope, ResolvedModelRoute
from app.agentive.harness.litellm_model import build_litellm_sdk_model
from app.agentive.harness.pydantic_ai_compat import Agent, UsageLimits
from app.schemas.agentive.model_dispatch import ModelDispatchInput
from app.schemas.agentive.work import WorkError
from app.schemas.agentive.work_price import ModelPriceRequest
from app.services.configured_model_evidence import (
    ConfiguredModelEvidence,
    TextModelAttestation,
    register_configured_model_evidence,
)


@pytest.fixture
def operator_policy():
    now = datetime.now(timezone.utc)
    scope = HarnessExecutionScope(
        tenant_id="workspace",
        principal_id="principal",
        workspace_id="workspace",
        thread_id="thread",
        session_id="session",
        run_id="run",
        permission_revision="review",
        capability_version="capabilities",
    )
    policy = TextModelAttestation.model_validate(
        {
            "workspace_id": scope.workspace_id,
            "route": {
                "provider": "openai",
                "model": "openai/synthetic",
                "credential_source": "platform",
                "credential_ref": "synthetic-generation",
            },
            "endpoint_fingerprint": hashlib.sha256(b"provider-default").hexdigest(),
            "policy_ref": "synthetic-policy",
            "source_ref": "synthetic-price-source",
            "account_terms_ref": "synthetic-account-terms",
            "valid_from": now - timedelta(seconds=10),
            "valid_until": now + timedelta(minutes=5),
            "max_payload_bytes": 1024,
            "max_output_parameter": 128,
            "input_tokens_upper": 1024,
            "output_tokens_upper": 256,
            "input_usd_per_million": "1",
            "output_usd_per_million": "2",
            "request_fee_upper_usd": "0.01",
        }
    )
    return scope, policy


@pytest.mark.asyncio
async def test_native_text_transport_uses_configured_evidence(
    operator_policy, monkeypatch
):
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", "/tmp/integral-litellm-test-cache")
    scope, policy = operator_policy
    evidence = ConfiguredModelEvidence((policy,))
    observed = []

    async def admit(dispatch):
        bounds = await evidence.bounds(dispatch)
        request = ModelPriceRequest(
            scope=scope,
            route=policy.route,
            request_id=dispatch.request_id,
            **bounds.model_dump(exclude={"valid_until"}),
            bounds_valid_until=bounds.valid_until,
        )
        priced = await evidence.price(request)
        assert priced.request_digest == request.digest()
        assert bounds.output_tokens_upper == 256
        observed.append("priced")

        async def ready():
            observed.append("ready")

        return ready

    async def sdk(**kwargs):
        observed.append("sdk")
        return {
            "id": "synthetic-response",
            "created": 1,
            "model": policy.route.model,
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Draft"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 12, "completion_tokens": 1, "total_tokens": 13},
        }

    async def observe(_event):
        pass

    model = build_litellm_sdk_model(
        route=ResolvedModelRoute(**policy.route.model_dump()),
        scope=scope,
        admission=admit,
        observer=observe,
        completion=sdk,
    )
    try:
        result = await Agent(model, instructions="Prepare a draft", retries=0).run(
            "Task data",
            model_settings={"max_tokens": 128},
            usage_limits=UsageLimits(request_limit=1),
        )
    finally:
        await model.client.close()
    assert result.output == "Draft"
    assert observed == ["priced", "ready", "sdk"]


@pytest.mark.parametrize(
    "change",
    [
        {"extra_body": {"reasoning": True}},
        {"tools": []},
        {"max_tokens": 129},
        {"max_completion_tokens": 1},
        {"messages": [{"role": "user", "content": [{"type": "image_url"}]}]},
        {"stream": True},
    ],
)
@pytest.mark.asyncio
async def test_configured_bounds_reject_unattested_payload(operator_policy, change):
    scope, policy = operator_policy
    evidence = ConfiguredModelEvidence((policy,))
    dispatch = ModelDispatchInput(
        scope=scope,
        route=policy.route.model_dump(),
        request_id="request",
        endpoint_fingerprint=policy.endpoint_fingerprint,
        sdk_payload_json=json.dumps(
            {
                "model": policy.route.model,
                "messages": [{"role": "user", "content": "task"}],
                "num_retries": 0,
                "max_tokens": 128,
                **change,
            }
        ),
    )
    with pytest.raises(WorkError, match="work.model_bounds_invalid"):
        await evidence.bounds(dispatch)


@pytest.mark.asyncio
async def test_configured_evidence_fences_endpoint_scope_and_price(operator_policy):
    scope, policy = operator_policy
    evidence = ConfiguredModelEvidence((policy,))
    dispatch = ModelDispatchInput(
        scope=scope,
        route=policy.route.model_dump(),
        request_id="request",
        endpoint_fingerprint=policy.endpoint_fingerprint,
        sdk_payload_json=json.dumps(
            {
                "model": policy.route.model,
                "messages": [{"role": "user", "content": "task"}],
                "num_retries": 0,
                "max_tokens": 128,
            }
        ),
    )
    with pytest.raises(WorkError, match="work.model_bounds_invalid"):
        await evidence.bounds(
            dispatch.model_copy(update={"endpoint_fingerprint": "0" * 64})
        )
    with pytest.raises(WorkError, match="work.model_evidence_unavailable"):
        await evidence.bounds(
            dispatch.model_copy(
                update={
                    "scope": scope.model_copy(
                        update={"workspace_id": "other", "tenant_id": "other"}
                    ),
                }
            )
        )
    bounds = await evidence.bounds(dispatch)
    request = ModelPriceRequest(
        scope=scope,
        route=policy.route,
        request_id=dispatch.request_id,
        **bounds.model_dump(exclude={"valid_until"}),
        bounds_valid_until=bounds.valid_until,
    )
    with pytest.raises(WorkError, match="work.model_bounds_invalid"):
        await evidence.price(request.model_copy(update={"output_tokens_upper": 1}))


def test_register_configured_model_evidence_preserves_host_ownership(
    operator_policy, monkeypatch, tmp_path
):
    from app.services import host_hooks

    _, policy = operator_policy
    monkeypatch.setattr(host_hooks, "_model_price_resolver", None)
    monkeypatch.setattr(host_hooks, "_model_bounds_resolver", None)
    path = tmp_path / "policy.json"
    path.write_text("[" + policy.model_dump_json() + "]")
    register_configured_model_evidence(str(path))
    assert host_hooks.get_model_price_resolver() is not None
    assert host_hooks.get_model_bounds_resolver() is not None
    with pytest.raises(ValueError, match="registered host resolver"):
        register_configured_model_evidence(str(path))
