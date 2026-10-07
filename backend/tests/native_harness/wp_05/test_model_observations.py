"""Encrypted append-only physical model request observation tests."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.agentive.harness.contracts import (
    HarnessExecutionScope,
    ModelUsageObservation,
    PhysicalModelRequest,
)
from app.agentive.harness.model_observations import (
    list_model_request_observations,
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
    assert len(model_request_rows) == 2


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
