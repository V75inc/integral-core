"""Append-only encrypted storage for physical model request observations."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal

from app.agentive.harness.contracts import (
    HarnessExecutionScope,
    PhysicalModelRequest,
    RunUsageSummary,
)
from app.agentive.harness.jvspatial_store import HarnessPersistenceError
from app.models.harness_records import HarnessModelRequestRecord
from app.schemas.agentive.work import WorkExecutionContext
from app.services.credential_crypto import (
    CIPHER_PREFIX_V1,
    decrypt_secret_from_storage,
    encrypt_secret_for_storage,
)


def _scope_key(scope: HarnessExecutionScope) -> str:
    identity = "\0".join(
        (scope.tenant_id, scope.principal_id, scope.thread_id, scope.session_id)
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _record_id(scope_key: str, request_id: str, outcome: str) -> str:
    key = f"{scope_key}:model-request:{request_id}:{outcome}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return f"o.HarnessModelRequestRecord.{digest}"


def _encode(observation: PhysicalModelRequest, *, record_id: str) -> str:
    payload = observation.model_dump(mode="json")
    try:
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return encrypt_secret_for_storage(serialized, aad=record_id)
    except RuntimeError as exc:
        raise HarnessPersistenceError(
            "Model request observations require configured storage encryption"
        ) from exc


def _decode(record: HarnessModelRequestRecord) -> PhysicalModelRequest:
    try:
        stored = record.payload_ciphertext
        if not stored.startswith(CIPHER_PREFIX_V1):
            raise ValueError("model observation is not encrypted")
        raw = decrypt_secret_from_storage(stored, aad=record.id)
        if not raw:
            raise ValueError("model observation decryption returned no content")
        return PhysicalModelRequest.model_validate_json(raw)
    except Exception as exc:
        raise HarnessPersistenceError(
            "Model request observation could not be authenticated or decoded"
        ) from exc


async def persist_model_request_observation(
    observation: PhysicalModelRequest,
    *,
    work_execution_context: WorkExecutionContext | None = None,
) -> None:
    """Append one dispatch/outcome transition idempotently.

    A dispatch intent is persisted before the LiteLLM call. Its terminal or
    uncertain observation uses a different deterministic ID, preserving both
    facts without rewriting history. A failed storage write propagates so the
    adapter can stop before model dispatch.
    """

    async def persist() -> None:
        scope_key = _scope_key(observation.scope)
        transition_key = f"{observation.request_id}:{observation.outcome}"
        record_id = _record_id(scope_key, observation.request_id, observation.outcome)
        encoded = _encode(observation, record_id=record_id)
        stored, created = await HarnessModelRequestRecord.create_if_absent(
            id=record_id,
            scope_key=scope_key,
            run_key=observation.scope.run_id,
            request_key=observation.request_id,
            transition_key=transition_key,
            occurred_at=(
                observation.observed_at or datetime.now(timezone.utc)
            ).isoformat(),
            outcome=observation.outcome,
            payload_ciphertext=encoded,
        )
        if not created and _decode(stored) != observation:
            raise HarnessPersistenceError(
                "Model request transition ID is already bound to different evidence"
            )

    if work_execution_context is None:
        await persist()
    else:
        from app.agentive.services.work_items import authorized_work_item_effect

        async with authorized_work_item_effect(work_execution_context):
            await persist()


async def list_model_request_observations(
    *, scope: HarnessExecutionScope
) -> list[PhysicalModelRequest]:
    """Load one execution scope's request transitions for reconciliation."""
    records = await HarnessModelRequestRecord.find(
        {"scope_key": _scope_key(scope), "run_key": scope.run_id}
    )
    observations = [_decode(record) for record in records]
    return sorted(
        observations,
        key=lambda item: (
            item.observed_at or item.dispatched_at,
            item.request_id,
            item.outcome,
        ),
    )


def unsettled_model_request_ids(
    observations: list[PhysicalModelRequest],
) -> list[str]:
    """Identify physical requests without a known terminal provider outcome."""
    grouped: dict[str, list[PhysicalModelRequest]] = {}
    for item in observations:
        grouped.setdefault(item.request_id, []).append(item)

    unsettled = []
    for request_id, transitions in grouped.items():
        latest = max(
            transitions,
            key=lambda item: (
                item.observed_at or item.dispatched_at,
                # Persisted timestamps may have coarse precision. If two
                # transitions cannot be ordered, uncertainty wins so recovery
                # never treats an ambiguous response as proof of settlement.
                {
                    "dispatch_intent": 0,
                    "responded": 1,
                    "failed": 1,
                    "cancelled": 1,
                    "outcome_unknown": 2,
                }[item.outcome],
            ),
        )
        if latest.outcome in {"dispatch_intent", "outcome_unknown"}:
            unsettled.append(request_id)
    return sorted(unsettled)


def summarize_model_usage(
    observations: list[PhysicalModelRequest],
) -> RunUsageSummary:
    """Aggregate unique model requests while retaining uncertainty explicitly."""
    grouped: dict[str, list[PhysicalModelRequest]] = {}
    for item in observations:
        grouped.setdefault(item.request_id, []).append(item)

    completed = 0
    unresolved = 0
    input_tokens = 0
    output_tokens = 0
    token_complete = True
    cost_complete = True
    known_costs: list[Decimal] = []

    for request_id, transitions in grouped.items():
        routes = {(item.provider, item.model) for item in transitions}
        scopes = {item.scope.model_dump_json() for item in transitions}
        if len(routes) != 1 or len(scopes) != 1:
            raise HarnessPersistenceError(
                f"Model request {request_id!r} changed route or execution scope"
            )

        responses = [item for item in transitions if item.outcome == "responded"]
        response = max(
            responses,
            key=lambda item: item.observed_at or item.dispatched_at,
            default=None,
        )
        if response is None:
            unresolved += 1
            token_complete = False
            cost_complete = False
            continue

        completed += 1
        usage = response.usage
        if usage is None:
            token_complete = False
            cost_complete = False
            continue
        if usage.input_tokens is not None:
            input_tokens += usage.input_tokens
        else:
            token_complete = False
        if usage.output_tokens is not None:
            output_tokens += usage.output_tokens
        else:
            token_complete = False
        if not usage.complete:
            token_complete = False
        if usage.provider_cost_usd is None:
            cost_complete = False
        else:
            known_costs.append(usage.provider_cost_usd)

    if unresolved:
        token_complete = False
        cost_complete = False
    if not grouped:
        token_complete = False
        cost_complete = False
    return RunUsageSummary(
        request_count=len(grouped),
        completed_request_count=completed,
        unresolved_request_count=unresolved,
        reported_input_tokens=input_tokens,
        reported_output_tokens=output_tokens,
        provider_cost_usd=(sum(known_costs, Decimal("0")) if known_costs else None),
        token_usage_complete=token_complete,
        provider_cost_complete=cost_complete,
    )
