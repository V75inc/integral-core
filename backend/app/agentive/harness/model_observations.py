"""Append-only encrypted storage for physical model request observations."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

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

    if work_execution_context is not None and observation.outcome != "dispatch_intent":
        # Accounting evidence describes an already-authorized dispatch. It must
        # remain appendable after Stop/lease loss; this grants no tool, output,
        # checkpoint, or additional model-dispatch authority. Bind it to the
        # encrypted original intent, rather than accepting a new scoped fact.
        intent_id = _record_id(
            _scope_key(observation.scope), observation.request_id, "dispatch_intent"
        )
        intent_record = await HarnessModelRequestRecord.get(intent_id)
        intent = _decode(intent_record) if intent_record is not None else None
        if intent is None or (
            intent.scope != observation.scope
            or intent.provider != observation.provider
            or intent.model != observation.model
            or intent.attempt != observation.attempt
            or intent.dispatched_at != observation.dispatched_at
            or observation.scope.principal_id != work_execution_context.principal_id
            or observation.scope.workspace_id != work_execution_context.workspace_id
            or observation.scope.thread_id != work_execution_context.thread_id
            or observation.scope.run_id != work_execution_context.run_id
        ):
            raise HarnessPersistenceError(
                "Model outcome does not match an authorized dispatch intent"
            )
        await persist()
    elif work_execution_context is None:
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


async def list_run_model_request_observations(
    *, run_id: str, principal_id: str, workspace_id: str, thread_id: str
) -> list[PhysicalModelRequest]:
    """Load accounting for an already-authorized Core run, including late facts.

    The caller must authorize the owning run first. Validate its immutable
    identity against every encrypted observation before projecting any data;
    never resolve through the thread's possibly newer active session.
    """
    records = await HarnessModelRequestRecord.find({"run_key": run_id})
    observations = [_decode(record) for record in records]
    for item in observations:
        if (
            item.scope.run_id != run_id
            or item.scope.principal_id != principal_id
            or item.scope.workspace_id != workspace_id
            or item.scope.thread_id != thread_id
        ):
            raise HarnessPersistenceError("Model accounting does not match its run")
    return observations


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


def summarize_model_context(
    observations: list[PhysicalModelRequest],
) -> list[dict[str, Any]]:
    """Content-free diagnostics for unique physical attempts, including late facts.

    Repeated schema characters are serialized input, not estimated billed
    tokens. Provider cache counts and cost remain nullable source observations.
    This does not create billable requests or infer a missing provider outcome.
    """
    grouped: dict[str, list[PhysicalModelRequest]] = {}
    for item in observations:
        grouped.setdefault(item.request_id, []).append(item)
    seen_schemas: set[str] = set()
    rows = []
    for request_id, transitions in sorted(
        grouped.items(), key=lambda pair: (pair[1][0].dispatched_at, pair[0])
    ):
        ordered = sorted(
            transitions, key=lambda item: item.observed_at or item.dispatched_at
        )
        latest = ordered[-1]
        context = next(
            (
                item.request_context
                for item in reversed(ordered)
                if item.request_context is not None
            ),
            None,
        )
        usage: dict[str, Any] = {}
        for item in ordered:
            if item.usage is not None:
                usage.update(item.usage.model_dump(mode="json", exclude_none=True))
        row = {
            "request_id": request_id,
            "model": latest.model,
            "attempt": latest.attempt,
            "outcome": latest.outcome,
            "elapsed_ms": (
                max(
                    0,
                    round(
                        (latest.observed_at - latest.dispatched_at).total_seconds()
                        * 1000
                    ),
                )
                if latest.observed_at is not None
                else None
            ),
            "usage": usage or None,
            **(context.model_dump(mode="json") if context is not None else {}),
        }
        fingerprint = context.tool_schema_fingerprint if context is not None else None
        row["repeated_tool_schema_chars"] = (
            context.tool_schema_chars
            if context is not None and fingerprint in seen_schemas
            else 0
        )
        if fingerprint:
            seen_schemas.add(fingerprint)
        rows.append(row)
    return rows


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
        else:
            completed += 1

        # Observations are cumulative facts about ONE physical request, not
        # independently billable calls. Prefer a complete response, otherwise
        # the latest known value for each field. A close/cancel observation
        # without usage must not erase earlier provider facts.
        usage_observations = sorted(
            (item for item in transitions if item.usage is not None),
            key=lambda item: (
                item.outcome == "responded" and bool(item.usage.complete),
                item.observed_at or item.dispatched_at,
                item.outcome == "responded",
            ),
            reverse=True,
        )

        def known(field, observations=usage_observations):
            return next(
                (
                    getattr(item.usage, field)
                    for item in observations
                    if getattr(item.usage, field) is not None
                ),
                None,
            )

        known_input = known("input_tokens")
        known_output = known("output_tokens")
        known_cost = known("provider_cost_usd")
        if known_input is not None:
            input_tokens += known_input
        if known_output is not None:
            output_tokens += known_output
        if (
            response is None
            or response.usage is None
            or not response.usage.complete
            or known_input is None
            or known_output is None
        ):
            token_complete = False
        if known_cost is None:
            cost_complete = False
        else:
            known_costs.append(known_cost)
            cost_evidence = next(
                item
                for item in usage_observations
                if item.usage.provider_cost_usd is not None
            )
            if cost_evidence.outcome != "responded" or not cost_evidence.usage.complete:
                cost_complete = False

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
