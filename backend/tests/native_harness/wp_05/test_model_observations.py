"""Encrypted append-only physical model request observation tests."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.agentive.harness.contracts import (
    HarnessExecutionScope,
    ModelUsageObservation,
    PhysicalModelRequest,
    ResolvedModelRoute,
)
from app.agentive.harness.model_observations import (
    list_model_request_observations,
    load_bound_model_request_observation,
    persist_model_request_observation,
    summarize_model_usage,
    unsettled_model_request_ids,
)
from app.models.harness_records import HarnessModelRequestRecord
from app.services.chat_providers.pydantic_ai_provider import (
    _model_observability_events,
)


@pytest.fixture
def model_request_rows(monkeypatch: pytest.MonkeyPatch):
    """Provide fake jvspatial Object persistence with model validation."""
    rows = {}
    monkeypatch.setattr(
        "app.services.credential_crypto._resolve_key", lambda: (b"k" * 32, None)
    )

    async def create_if_absent(cls, **kwargs):
        current = rows.get(kwargs["id"])
        if current is not None:
            return current, False
        current = cls(**kwargs)
        rows[current.id] = current
        return current, True

    async def find(cls, query=None, **kwargs):
        filters = {**(query or {}), **kwargs}
        return [
            row
            for row in rows.values()
            if all(getattr(row, key) == value for key, value in filters.items())
        ]

    async def get(cls, record_id):
        return rows.get(record_id)

    monkeypatch.setattr(
        HarnessModelRequestRecord,
        "create_if_absent",
        classmethod(create_if_absent),
    )
    monkeypatch.setattr(HarnessModelRequestRecord, "find", classmethod(find))
    monkeypatch.setattr(HarnessModelRequestRecord, "get", classmethod(get))
    return rows


def _scope() -> HarnessExecutionScope:
    return HarnessExecutionScope(
        tenant_id="workspace-1",
        principal_id="user-1",
        workspace_id="workspace-1",
        thread_id="thread-1",
        session_id="session-1",
        run_id="run-1",
        permission_revision="permissions-1",
        capability_version="tools-1",
    )


def _observation(outcome: str = "dispatch_intent") -> PhysicalModelRequest:
    return PhysicalModelRequest(
        request_id="request-1",
        scope=_scope(),
        provider="anthropic",
        model="anthropic/claude-sonnet",
        attempt=1,
        dispatched_at=datetime.now(timezone.utc),
        observed_at=datetime.now(timezone.utc),
        outcome=outcome,
        provider_request_id="provider-1" if outcome == "responded" else None,
        usage=(
            ModelUsageObservation(
                input_tokens=10,
                output_tokens=2,
                cost_source="unavailable",
                complete=True,
            )
            if outcome == "responded"
            else None
        ),
    )


def test_public_call_metadata_has_canonical_provider_model_id() -> None:
    """Typed call details avoid duplicate prefixes and keep unknown cost clear."""
    observation = _observation("responded").model_copy(
        update={"provider": "ollama_chat", "model": "ollama_chat/gemma4:26b"}
    )

    [event] = _model_observability_events([observation])

    assert event["modelId"] == "ollama_chat/gemma4:26b"
    assert event["provider"] == "ollama_chat"
    assert event["outcome"] == "responded"
    assert event["costSource"] == "unavailable"


@pytest.mark.asyncio
async def test_model_attempt_transitions_are_encrypted_and_append_only(
    model_request_rows,
) -> None:
    """Dispatch intent and response facts use separate immutable records."""
    dispatched = _observation("dispatch_intent")
    responded = _observation("responded")
    await persist_model_request_observation(dispatched)
    await persist_model_request_observation(responded)
    await persist_model_request_observation(responded)

    assert len(model_request_rows) == 2
    records = list(model_request_rows.values())
    assert {record.outcome for record in records} == {"dispatch_intent", "responded"}
    assert all(record.payload_ciphertext.startswith("v1:") for record in records)
    assert all(
        "anthropic/claude-sonnet" not in record.payload_ciphertext for record in records
    )
    restored = await list_model_request_observations(scope=_scope())
    assert [item.outcome for item in restored] == [
        "dispatch_intent",
        "responded",
    ]


@pytest.mark.asyncio
async def test_model_attempt_transition_conflict_cannot_rewrite_history(
    model_request_rows,
) -> None:
    """Reusing a transition ID with different evidence is rejected."""
    original = _observation("responded")
    await persist_model_request_observation(original)
    conflicting = original.model_copy(update={"provider_request_id": "provider-other"})

    with pytest.raises(RuntimeError, match="already bound to different evidence"):
        await persist_model_request_observation(conflicting)

    assert len(model_request_rows) == 1


def test_usage_summary_deduplicates_request_and_retains_unknown_outcome() -> None:
    """A request contributes once and unknown dispatch cost stays unresolved."""
    dispatched = _observation("dispatch_intent")
    response = _observation("responded").model_copy(
        update={
            "usage": ModelUsageObservation(
                input_tokens=10,
                output_tokens=2,
                provider_cost_usd=Decimal("0.0045"),
                cost_source="litellm_response",
                complete=True,
            )
        }
    )
    uncertain = _observation("outcome_unknown").model_copy(
        update={"request_id": "request-2", "provider_request_id": None, "usage": None}
    )

    summary = summarize_model_usage([dispatched, response, response, uncertain])

    assert summary.request_count == 2
    assert summary.completed_request_count == 1
    assert summary.unresolved_request_count == 1
    assert summary.reported_input_tokens == 10
    assert summary.reported_output_tokens == 2
    assert summary.provider_cost_usd == Decimal("0.0045")
    assert summary.token_usage_complete is False
    assert summary.provider_cost_complete is False


def test_unsettled_request_detection_blocks_unknown_replay_only() -> None:
    """Intent/unknown requests block recovery; known terminal outcomes do not."""
    intent = _observation("dispatch_intent")
    responded = _observation("responded")
    failed = _observation("failed").model_copy(update={"request_id": "request-failed"})
    cancelled = _observation("cancelled").model_copy(
        update={"request_id": "request-cancelled"}
    )
    uncertain = _observation("outcome_unknown").model_copy(
        update={"request_id": "request-unknown"}
    )

    assert unsettled_model_request_ids(
        [intent, responded, failed, cancelled, uncertain]
    ) == ["request-unknown"]
    assert unsettled_model_request_ids([intent]) == ["request-1"]


def test_unsettled_detection_prefers_unknown_when_timestamps_collide() -> None:
    """Equal persisted timestamps cannot turn an ambiguous attempt into safe."""
    timestamp = datetime.now(timezone.utc)
    responded = _observation("responded").model_copy(update={"observed_at": timestamp})
    unknown = _observation("outcome_unknown").model_copy(
        update={"observed_at": timestamp}
    )

    assert unsettled_model_request_ids([responded, unknown]) == ["request-1"]


@pytest.mark.parametrize("outcome", ["cancelled", "failed", "outcome_unknown"])
def test_partial_usage_survives_interruption(outcome: str) -> None:
    partial = _observation(outcome).model_copy(
        update={
            "usage": ModelUsageObservation(
                input_tokens=100,
                output_tokens=12,
                provider_cost_usd=Decimal("0.004"),
                cost_source="litellm_response",
                complete=False,
            )
        }
    )
    summary = summarize_model_usage([_observation(), partial, partial])
    assert summary.request_count == 1
    assert summary.reported_input_tokens == 100
    assert summary.reported_output_tokens == 12
    assert summary.provider_cost_usd == Decimal("0.004")
    assert not summary.token_usage_complete
    assert not summary.provider_cost_complete


def test_complete_response_reconciles_partial_usage_once() -> None:
    partial = _observation("cancelled").model_copy(
        update={
            "usage": ModelUsageObservation(
                input_tokens=100,
                output_tokens=12,
                provider_cost_usd=Decimal("0.004"),
                cost_source="litellm_response",
                complete=False,
            )
        }
    )
    complete = _observation("responded").model_copy(
        update={
            "usage": ModelUsageObservation(
                input_tokens=100,
                output_tokens=20,
                provider_cost_usd=Decimal("0.006"),
                cost_source="litellm_response",
                complete=True,
            )
        }
    )
    summary = summarize_model_usage(
        [partial, complete, complete, _observation("cancelled")]
    )
    assert summary.reported_input_tokens == 100
    assert summary.reported_output_tokens == 20
    assert summary.provider_cost_usd == Decimal("0.006")
    assert summary.token_usage_complete
    assert summary.provider_cost_complete


def test_response_without_cost_keeps_earlier_known_cost() -> None:
    partial = _observation("cancelled").model_copy(
        update={
            "usage": ModelUsageObservation(
                input_tokens=10,
                provider_cost_usd=Decimal("0.004"),
                cost_source="litellm_response",
                complete=False,
            )
        }
    )
    summary = summarize_model_usage([partial, _observation("responded")])
    assert summary.provider_cost_usd == Decimal("0.004")
    assert summary.reported_input_tokens == 10
    assert summary.reported_output_tokens == 2
    assert not summary.provider_cost_complete


def test_public_call_metadata_retains_known_usage_on_later_empty_close() -> None:
    partial = _observation("cancelled").model_copy(
        update={
            "usage": ModelUsageObservation(
                input_tokens=100,
                output_tokens=12,
                provider_cost_usd=Decimal("0.004"),
                cost_source="litellm_response",
                complete=False,
            )
        }
    )
    [event] = _model_observability_events([partial, _observation("outcome_unknown")])
    assert event["usage"] == {"inputTokens": 100, "outputTokens": 12}
    assert event["providerCostUsd"] == 0.004
    assert event["costSource"] == "litellm_response"
    assert event["usageComplete"] is False
    assert event["costComplete"] is False


@pytest.mark.asyncio
async def test_cancelled_work_can_append_only_existing_dispatch_accounting(
    model_request_rows,
    monkeypatch,
) -> None:
    """Stop fences new dispatches, while their final cost evidence survives."""
    from contextlib import asynccontextmanager

    from app.schemas.agentive.work import WorkError, WorkExecutionContext

    context = WorkExecutionContext(
        work_item_id="work-1",
        attempt=1,
        run_id="run-1",
        principal_id="user-1",
        workspace_id="workspace-1",
        thread_id="thread-1",
        logical_step_key="model",
        effect_key="effect-1",
        lease_token="lease-1",
        lease_fence=1,
        cancellation_signal=True,
    )

    @asynccontextmanager
    async def cancelled(_context):
        raise WorkError("work.cancelled", "cancel requested")
        yield

    monkeypatch.setattr(
        "app.agentive.services.work_items.authorized_work_item_effect", cancelled
    )
    intent = _observation()
    await persist_model_request_observation(intent)
    terminal = intent.model_copy(
        update={
            "outcome": "cancelled",
            "observed_at": datetime.now(timezone.utc),
            "usage": ModelUsageObservation(
                input_tokens=12,
                output_tokens=3,
                provider_cost_usd=Decimal("0.002"),
                cost_source="provider_response",
                complete=False,
            ),
        }
    )
    await persist_model_request_observation(terminal, work_execution_context=context)
    restored = await list_model_request_observations(scope=_scope())
    assert unsettled_model_request_ids(restored) == []
    summary = summarize_model_usage(restored)
    assert summary.reported_input_tokens == 12
    assert summary.provider_cost_usd == Decimal("0.002")
    assert not summary.token_usage_complete

    with pytest.raises(WorkError):
        await persist_model_request_observation(
            intent.model_copy(update={"request_id": "new-request"}),
            work_execution_context=context,
        )
    with pytest.raises(RuntimeError, match="authorized dispatch intent"):
        await persist_model_request_observation(
            terminal.model_copy(update={"request_id": "never-authorized"}),
            work_execution_context=context,
        )
    with pytest.raises(RuntimeError, match="authorized dispatch intent"):
        await persist_model_request_observation(
            terminal.model_copy(update={"model": "another-model"}),
            work_execution_context=context,
        )
    for changed in (
        {"credential_source": "platform"},
        {"credential_source": "workspace_byok", "credential_ref": "another-credential"},
    ):
        with pytest.raises(RuntimeError, match="authorized dispatch intent"):
            await persist_model_request_observation(
                terminal.model_copy(update=changed), work_execution_context=context
            )
    assert len(model_request_rows) == 2


def _receipt_route() -> ResolvedModelRoute:
    return ResolvedModelRoute(
        provider="anthropic",
        model="anthropic/claude-sonnet",
        api_key="synthetic-key-never-stored",
        credential_source="workspace_byok",
        credential_ref="credential-1",
    )


def _attributed_intent() -> PhysicalModelRequest:
    return _observation().model_copy(
        update={"credential_source": "workspace_byok", "credential_ref": "credential-1"}
    )


@pytest.mark.parametrize("value", ["", " credential-1", "credential-1 "])
def test_observation_credential_reference_is_canonical(value):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PhysicalModelRequest.model_validate(
            _attributed_intent().model_dump() | {"credential_ref": value}
        )


def test_credential_reference_without_source_is_not_attribution():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PhysicalModelRequest.model_validate(
            _attributed_intent().model_dump() | {"credential_source": None}
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome", ["responded", "failed", "cancelled", "outcome_unknown"]
)
async def test_exact_route_receipt_preserves_encrypted_attribution(
    model_request_rows, outcome
):
    intent = _attributed_intent()
    terminal = intent.model_copy(
        update={"outcome": outcome, "observed_at": datetime.now(timezone.utc)}
    )
    await persist_model_request_observation(intent)
    await persist_model_request_observation(terminal)
    receipt = await load_bound_model_request_observation(
        scope=_scope(),
        request_id=intent.request_id,
        outcome=outcome,
        expected_route=_receipt_route(),
    )
    assert receipt == terminal
    assert receipt.usage is None  # Evidence lookup never fabricates a cost.
    assert all(
        "credential-1" not in row.payload_ciphertext
        for row in model_request_rows.values()
    )
    assert all(
        "synthetic-key-never-stored" not in row.model_dump_json()
        for row in model_request_rows.values()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"provider": "openai"},
        {"model": "other-model"},
        {"credential_source": "platform", "api_key": None},
        {"credential_ref": "credential-other"},
        {"credential_ref": None},
    ],
)
async def test_receipt_cannot_substitute_route_or_credential(
    model_request_rows, change
):
    intent = _attributed_intent()
    await persist_model_request_observation(intent)
    await persist_model_request_observation(
        intent.model_copy(update={"outcome": "responded"})
    )
    with pytest.raises(RuntimeError):
        await load_bound_model_request_observation(
            scope=_scope(),
            request_id=intent.request_id,
            outcome="responded",
            expected_route=_receipt_route().model_copy(update=change),
        )


@pytest.mark.asyncio
async def test_legacy_unknown_attribution_is_readable_but_not_exact_route_proof(
    model_request_rows,
):
    intent = _observation()
    await persist_model_request_observation(intent)
    await persist_model_request_observation(
        intent.model_copy(update={"outcome": "responded"})
    )
    restored = await list_model_request_observations(scope=_scope())
    assert all(
        item.credential_source is None and item.credential_ref is None
        for item in restored
    )
    with pytest.raises(RuntimeError, match="route mismatch"):
        await load_bound_model_request_observation(
            scope=_scope(),
            request_id=intent.request_id,
            outcome="responded",
            expected_route=_receipt_route(),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field", ["scope_key", "run_key", "request_key", "transition_key", "outcome"]
)
async def test_receipt_rejects_outer_record_identity_drift(model_request_rows, field):
    intent = _attributed_intent()
    await persist_model_request_observation(intent)
    await persist_model_request_observation(
        intent.model_copy(update={"outcome": "responded"})
    )
    row = next(row for row in model_request_rows.values() if row.outcome == "responded")
    setattr(row, field, "counterfeit")
    with pytest.raises(RuntimeError, match="route mismatch"):
        await load_bound_model_request_observation(
            scope=_scope(),
            request_id=intent.request_id,
            outcome="responded",
            expected_route=_receipt_route(),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed",
    [
        {"attempt": 2},
        {"dispatched_at": datetime(2025, 1, 1, tzinfo=timezone.utc)},
        {"observed_at": None},
        {"observed_at": datetime(2025, 1, 1)},
    ],
)
async def test_receipt_requires_matching_dispatch_and_aware_outcome(
    model_request_rows, changed
):
    intent = _attributed_intent()
    await persist_model_request_observation(intent)
    await persist_model_request_observation(
        intent.model_copy(update={"outcome": "responded", **changed})
    )
    with pytest.raises(RuntimeError, match="does not match dispatch intent"):
        await load_bound_model_request_observation(
            scope=_scope(),
            request_id=intent.request_id,
            outcome="responded",
            expected_route=_receipt_route(),
        )


@pytest.mark.asyncio
async def test_receipt_requires_both_transitions_and_original_scope(model_request_rows):
    intent = _attributed_intent()
    await persist_model_request_observation(intent)
    with pytest.raises(RuntimeError, match="incomplete"):
        await load_bound_model_request_observation(
            scope=_scope(),
            request_id=intent.request_id,
            outcome="responded",
            expected_route=_receipt_route(),
        )
    await persist_model_request_observation(
        intent.model_copy(update={"outcome": "responded"})
    )
    for field in (
        "tenant_id",
        "principal_id",
        "workspace_id",
        "thread_id",
        "session_id",
        "run_id",
    ):
        with pytest.raises(RuntimeError):
            await load_bound_model_request_observation(
                scope=_scope().model_copy(update={field: "other"}),
                request_id=intent.request_id,
                outcome="responded",
                expected_route=_receipt_route(),
            )


@pytest.mark.asyncio
async def test_run_accounting_binds_original_identity_after_session_changes(
    model_request_rows,
) -> None:
    from app.agentive.harness.jvspatial_store import HarnessPersistenceError
    from app.agentive.harness.model_observations import (
        list_run_model_request_observations,
    )

    observation = _observation("responded")
    await persist_model_request_observation(observation)
    loaded = await list_run_model_request_observations(
        run_id="run-1",
        principal_id="user-1",
        workspace_id="workspace-1",
        thread_id="thread-1",
    )
    assert loaded == [observation]
    with pytest.raises(HarnessPersistenceError, match="does not match"):
        await list_run_model_request_observations(
            run_id="run-1",
            principal_id="foreign-user",
            workspace_id="workspace-1",
            thread_id="thread-1",
        )


def test_context_diagnostics_deduplicate_attempts_and_preserve_cache_and_cost():
    from datetime import timedelta

    from app.agentive.harness.contracts import ModelRequestContextObservation
    from app.agentive.harness.model_observations import summarize_model_context

    first = _observation("dispatch_intent").model_copy(
        update={
            "observed_at": None,
            "request_context": ModelRequestContextObservation(
                instruction_chars=100,
                conversation_chars=50,
                tool_result_chars=20,
                tool_schema_chars=400,
                message_count=4,
                visible_tool_count=2,
                tool_schema_fingerprint="schema-a",
            ),
        }
    )
    responded = first.model_copy(
        update={
            "outcome": "responded",
            "observed_at": first.dispatched_at + timedelta(seconds=2),
            "usage": ModelUsageObservation(
                input_tokens=10,
                output_tokens=2,
                cached_input_tokens=4,
                provider_cost_usd=Decimal("0.001"),
                cost_source="provider_response",
                complete=True,
            ),
        }
    )
    second = responded.model_copy(
        update={
            "request_id": "request-2",
            "dispatched_at": first.dispatched_at + timedelta(seconds=3),
            "observed_at": first.dispatched_at + timedelta(seconds=4),
        }
    )
    rows = summarize_model_context([second, responded, first])
    assert len(rows) == 2
    assert rows[0]["repeated_tool_schema_chars"] == 0
    assert rows[1]["repeated_tool_schema_chars"] == 400
    assert rows[0]["elapsed_ms"] == 2000
    assert rows[0]["usage"]["cached_input_tokens"] == 4
    assert rows[0]["usage"]["provider_cost_usd"] == "0.001"
