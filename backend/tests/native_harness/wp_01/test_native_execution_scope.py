"""Host identity mapping for a native Harness invocation."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.agentive.harness.contracts import (
    HarnessExecutionScope,
    ModelUsageObservation,
    PhysicalModelRequest,
    ResolvedModelRoute,
)


def _scope(**overrides) -> HarnessExecutionScope:
    values = {
        "tenant_id": "workspace-1",
        "principal_id": "user-1",
        "workspace_id": "workspace-1",
        "thread_id": "thread-1",
        "session_id": "session-1",
        "run_id": "run-1",
        "permission_revision": "permissions-8",
        "capability_version": "tools-4",
    }
    values.update(overrides)
    return HarnessExecutionScope(**values)


def test_execution_scope_maps_only_core_owned_ids() -> None:
    """Framework run and conversation IDs come from immutable Core scope."""
    scope = _scope()
    assert scope.framework_conversation_id == "session-1"
    assert scope.framework_run_id == "run-1"
    with pytest.raises(ValidationError):
        scope.run_id = "model-selected-run"  # type: ignore[misc]


def test_execution_scope_rejects_cross_workspace_tenant_mapping() -> None:
    """A store namespace cannot be rebound to another workspace."""
    with pytest.raises(ValidationError, match="tenant scope must match"):
        _scope(tenant_id="workspace-other")


@pytest.mark.parametrize(
    "field",
    ["tenant_id", "principal_id", "workspace_id", "thread_id", "session_id", "run_id"],
)
def test_execution_scope_requires_nonempty_ids(field: str) -> None:
    """Every host identity and revision is required for a Harness run."""
    with pytest.raises(ValidationError, match="cannot be empty"):
        _scope(**{field: ""})


def test_execution_scope_rejects_whitespace_padded_ids() -> None:
    """Identity keys have one canonical spelling for store lookups."""
    with pytest.raises(ValidationError, match="whitespace-padded"):
        _scope(session_id=" session-1")


def test_execution_scope_has_json_schema_and_round_trips() -> None:
    """The host scope has a serializable, versionable contract shape."""
    scope = _scope()
    encoded = scope.model_dump_json()
    decoded = HarnessExecutionScope.model_validate_json(encoded)

    assert decoded == scope
    assert set(HarnessExecutionScope.model_json_schema()["properties"]) == {
        "tenant_id",
        "principal_id",
        "workspace_id",
        "thread_id",
        "session_id",
        "run_id",
        "permission_revision",
        "capability_version",
    }


def test_model_route_keeps_workspace_credential_out_of_serialized_contract() -> None:
    """The SDK can receive a key without putting it in telemetry or JSON."""
    route = ResolvedModelRoute(
        provider="anthropic",
        model="anthropic/claude-sonnet",
        api_key="never-log-this",
        credential_source="workspace_byok",
        credential_ref="credential-42",
    )

    assert route.api_key.get_secret_value() == "never-log-this"
    assert "never-log-this" not in route.model_dump_json()
    assert "**********" in route.model_dump_json()

    local = ResolvedModelRoute(
        provider="ollama",
        model="ollama/model-name",
        credential_source="local",
    )
    assert local.api_key is None
    with pytest.raises(ValidationError, match="must not carry an API key"):
        ResolvedModelRoute(
            provider="ollama",
            model="ollama/model-name",
            api_key="unexpected",
            credential_source="local",
        )


def test_model_usage_keeps_unknown_cost_distinct_from_zero_cost() -> None:
    """Missing cost is not misreported as a confirmed zero."""
    usage = ModelUsageObservation(
        input_tokens=12,
        output_tokens=5,
        cached_input_tokens=2,
        reasoning_tokens=None,
        provider_cost_usd=None,
        cost_source="unavailable",
        complete=False,
    )

    assert usage.provider_cost_usd is None
    assert usage.input_tokens == 12
    assert usage.complete is False


def test_physical_model_request_binds_route_usage_and_immutable_scope() -> None:
    """A physical attempt has its own ID and cannot be rebound after dispatch."""
    attempt = PhysicalModelRequest(
        request_id="request-1",
        scope=_scope(),
        provider="anthropic",
        model="anthropic/claude-sonnet",
        attempt=1,
        dispatched_at=datetime.now(timezone.utc),
        outcome="responded",
        provider_request_id="provider-request-9",
        usage=ModelUsageObservation(
            input_tokens=12,
            output_tokens=5,
            provider_cost_usd=Decimal("0.002"),
            cost_source="litellm_response",
            complete=True,
        ),
    )

    assert (
        PhysicalModelRequest.model_validate_json(attempt.model_dump_json()) == attempt
    )
    with pytest.raises(ValidationError):
        attempt.outcome = "failed"  # type: ignore[misc]
