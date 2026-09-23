"""Durable, Core-owned lifecycle records for harness executions.

This is the first Harness Kernel slice. A provider session remains an adapter
continuation handle; an ``AgentRun`` is the authoritative Integral record for
one turn of governed work.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Dict, Literal, Optional
from uuid import uuid4

from jvspatial.core import Object
from jvspatial.core.annotations import attribute
from pydantic import Field

from app.utils.time import utc_now_iso

RunStatus = Literal["running", "succeeded", "failed", "cancelled"]
RunStepStatus = Literal[
    "running",
    "succeeded",
    "failed",
    "waiting_for_human",
    "cancelled",
    "denied",
]
_TERMINAL = frozenset({"succeeded", "failed", "cancelled"})
_STEP_TERMINAL = frozenset(
    {"succeeded", "failed", "cancelled", "denied", "waiting_for_human"}
)
_OBSERVABILITY_VERSION = "v1"


def _elapsed_ms(started_at: str, finished_at: Optional[str]) -> Optional[float]:
    """Return a clock duration only when both durable timestamps parse."""
    if not started_at or not finished_at:
        return None
    try:
        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        finish = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0.0, (finish - start).total_seconds() * 1000)


class AgentRun(Object):
    """Execution authority record held outside the graph."""

    run_id: str = attribute(default="", indexed=True)
    thread_id: str = ""
    user_id: str = ""
    workspace_id: str = ""
    app_id: Optional[str] = None
    provider_id: str = ""
    agent_id: str = ""
    origin: str = "chat"
    status: RunStatus = "running"
    capability_version: str = "tool-manifest-v1"
    capability_snapshot: Dict[str, Any] = Field(default_factory=dict)
    started_at: str = ""
    finished_at: Optional[str] = None
    error: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    work_item_id: str = ""
    deadline_at: str = ""


class RunStep(Object):
    """Authoritative capability receipt for one execution boundary."""

    run_id: str = attribute(default="", indexed=True)
    step_key: str = ""
    kind: str = ""
    name: str = ""
    status: RunStepStatus = "running"
    input_fingerprint: Optional[str] = None
    output_fingerprint: Optional[str] = None
    error_code: Optional[str] = None
    started_at: str = ""
    finished_at: Optional[str] = None
    capability_key: str = ""
    capability_version: str = ""
    capability_source: str = ""
    app_id: Optional[str] = None
    connector_id: Optional[str] = None
    origin: str = ""
    principal_id: str = ""
    workspace_id: str = ""
    idempotency_key: str = attribute(default="", indexed=True)
    attempt: int = 1
    policy_decision: str = ""
    denial_code: Optional[str] = None
    approval_ref: Optional[str] = None
    snapshot_fingerprint: str = ""
    snapshot_divergence: bool = False
    result_class: str = ""
    adapter_error_code: Optional[str] = None
    duration_ms: Optional[float] = None
    result_json: str = ""
    work_item_id: str = ""


async def start_run(
    *,
    thread_id: str,
    user_id: str,
    workspace_id: str,
    provider_id: str,
    agent_id: str = "",
    origin: str = "chat",
    app_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    run_id: Optional[str] = None,
    work_item_id: str = "",
    deadline_at: str = "",
) -> AgentRun:
    """Persist a run before streaming a provider turn."""
    snapshot = await build_capability_snapshot(workspace_id)
    return await AgentRun.create(
        run_id=run_id or str(uuid4()),
        thread_id=thread_id,
        user_id=user_id,
        workspace_id=workspace_id or "",
        app_id=app_id,
        provider_id=provider_id,
        agent_id=agent_id,
        origin=origin,
        status="running",
        capability_version=str(snapshot["manifest_version"]),
        capability_snapshot=snapshot,
        started_at=utc_now_iso(),
        metadata=dict(metadata or {}),
        work_item_id=work_item_id or "",
        deadline_at=deadline_at or "",
    )


def build_core_capability_snapshot() -> Dict[str, Any]:
    """Return the immutable, dispatchable Core capability contract for a run.

    The snapshot is intentionally Core-only in this slice. App and environment
    capability declarations will join it after their registries become
    persisted sources of truth rather than process-local maps.
    """
    import yaml

    from app.agentive.tooling.catalogue import build_tool_catalogue
    from app.agentive.tooling.manifest import DEFAULT_MANIFEST_PATH

    manifest_raw = yaml.safe_load(DEFAULT_MANIFEST_PATH.read_text()) or {}
    capabilities = [
        {
            "name": item["name"],
            "op_class": item["op_class"],
            "policy_action": item.get("policy_action"),
        }
        for item in build_tool_catalogue()
    ]
    capabilities.sort(key=lambda item: str(item["name"]))
    version = manifest_raw.get("manifest_version") or "unknown"
    return {"manifest_version": str(version), "capabilities": capabilities}


def _operation_snapshot(operation: Dict[str, Any]) -> Dict[str, Any]:
    """Keep a public operation declaration without copying executable code."""
    return {
        "key": str(operation.get("key") or ""),
        "kind": str(operation.get("kind") or "execute"),
        "capability": operation.get("capability"),
        "policy_action": operation.get("policy_action"),
        "staging_level": operation.get("staging_level"),
    }


def _query_snapshot(
    query: Dict[str, Any],
    *,
    operational_model_id: str,
    package_slug: str,
    package_version: str,
) -> Dict[str, Any]:
    """Bind a fixed App query declaration and its install provenance."""
    return {
        "key": str(query.get("key") or ""),
        "kind": "read",
        "handler_key": str(query.get("handler_key") or ""),
        "input_schema": dict(query.get("input_schema") or {}),
        "output_schema": dict(query.get("output_schema") or {}),
        "query_template": dict(query.get("query_template") or {}),
        "package_version": package_version,
        "provenance": {
            "source": "operational_model",
            "operational_model_id": operational_model_id,
            "package_slug": package_slug,
            "package_version": package_version,
        },
    }


def _finalize_snapshot(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """Sort a snapshot and bind its declaration set with a fingerprint."""
    snapshot["capabilities"].sort(key=lambda item: str(item["name"]))
    snapshot["apps"].sort(key=lambda item: str(item["app_id"]))
    snapshot["environments"].sort(key=lambda item: str(item["connector_id"]))
    snapshot["unresolved_apps"].sort(key=lambda item: str(item["app_id"]))
    canonical = json.dumps(snapshot, separators=(",", ":"), sort_keys=True)
    return {
        **snapshot,
        "fingerprint": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


async def build_capability_snapshot(workspace_id: str) -> Dict[str, Any]:
    """Record persisted App and connector declarations available to a run.

    Process-local dispatch tables remain execution caches while startup
    rehydration exists.  They are deliberately not consulted here: a run
    record must remain useful after a process restart and reveal declarations
    that failed to mount.  This is an audit/conformance snapshot, not yet a
    dispatch authorization decision; CapabilityBroker will make that decision
    in the next kernel phase.
    """
    from app.agentive.nodes import Connector
    from app.models.nodes import App
    from app.services.app_graph import get_app_attached_operational_model
    from app.services.application_definitions import get_active_application_definition
    from app.services.operational_model_runtime import compile_canonical_manifest

    core = build_core_capability_snapshot()
    snapshot: Dict[str, Any] = {
        "manifest_version": core["manifest_version"],
        "capabilities": core["capabilities"],
        "apps": [],
        "environments": [],
        "unresolved_apps": [],
    }

    active_app_query: Dict[str, str] = {
        "workspace_id": workspace_id,
        "lifecycle_state": "active",
    }
    apps = await App.find(active_app_query)
    for app in apps:
        app_id = str(getattr(app, "id", ""))
        profile = await get_app_attached_operational_model(app)
        definition = await get_active_application_definition(app)
        if definition is not None and getattr(definition, "canonical_manifest", None):
            canonical = dict(definition.canonical_manifest)
            operational_model_id = str(
                getattr(definition, "source_operational_model_id", "")
                or getattr(profile, "id", "")
                or ""
            )
        elif profile is None:
            snapshot["unresolved_apps"].append(
                {"app_id": app_id, "reason": "active_definition_missing"}
            )
            continue
        else:
            try:
                canonical = compile_canonical_manifest(
                    manifest=dict(getattr(profile, "manifest", {}) or {}),
                    scope_hint="app",
                )
                operational_model_id = str(getattr(profile, "id", "") or "")
            except Exception:
                # Do not make a provider turn unavailable merely because this
                # non-authoritative snapshot cannot compile a legacy declaration.
                snapshot["unresolved_apps"].append(
                    {"app_id": app_id, "reason": "manifest_unavailable"}
                )
                continue
        app_operations = (canonical.get("app") or {}).get("operations") or []
        operations = [
            _operation_snapshot(operation)
            for operation in app_operations
            if isinstance(operation, dict)
        ]
        operations.sort(key=lambda item: item["key"])
        package_slug = (
            getattr(app, "installed_package_slug", None)
            or getattr(app, "source_operational_model_slug", None)
            or ""
        )
        package_version = (
            getattr(app, "installed_package_version", None)
            or getattr(app, "version", None)
            or ""
        )
        queries = [
            _query_snapshot(
                query,
                operational_model_id=operational_model_id,
                package_slug=str(package_slug),
                package_version=str(package_version),
            )
            for query in (canonical.get("app") or {}).get("queries") or []
            if isinstance(query, dict)
        ]
        queries.sort(key=lambda item: item["key"])
        snapshot["apps"].append(
            {
                "app_id": app_id,
                "package_slug": package_slug,
                "package_version": package_version,
                "operations": operations,
                "queries": queries,
            }
        )

    connectors = await Connector.find({"workspace_id": workspace_id})
    for connector in connectors:
        connector_capabilities = getattr(connector, "capabilities", []) or []
        connector_permissions = getattr(connector, "permissions", []) or []
        connector_health = getattr(connector, "health_status", "unknown")
        auth_state = getattr(connector, "auth_state", None) or {}
        discovered = (
            auth_state.get("discovered_tools") if isinstance(auth_state, dict) else None
        )
        tool_keys = []
        if isinstance(discovered, list):
            from app.agentive.connectors.mcp_mount import tool_key_for

            connector_id = str(getattr(connector, "id", ""))
            tool_keys = sorted(
                {
                    tool_key_for(connector_id, str(item.get("name") or ""))
                    for item in discovered
                    if isinstance(item, dict) and item.get("name")
                }
            )
        snapshot["environments"].append(
            {
                "connector_id": str(getattr(connector, "id", "")),
                "kind": str(getattr(connector, "kind", "")),
                "subclass_slug": str(getattr(connector, "subclass_slug", "")),
                "capabilities": sorted(connector_capabilities),
                "tool_keys": tool_keys,
                "permissions": sorted(connector_permissions),
                "health_status": str(connector_health),
            }
        )
    from app.services.app_operations.registry import list_workspace_operations

    apps_by_id = {str(item.get("app_id") or ""): item for item in snapshot["apps"]}
    for app_id, ops in list_workspace_operations(workspace_id).items():
        extra = [
            _operation_snapshot(operation)
            for operation in ops.values()
            if isinstance(operation, dict)
        ]
        existing = apps_by_id.get(app_id)
        if existing is None:
            extra.sort(key=lambda item: item["key"])
            snapshot["apps"].append(
                {
                    "app_id": app_id,
                    "package_slug": "",
                    "package_version": "registry-cache",
                    "operations": extra,
                    "queries": [],
                }
            )
            continue
        have = {str(item.get("key") or "") for item in existing.get("operations") or []}
        for operation in extra:
            if operation["key"] not in have:
                existing["operations"].append(operation)
        existing["operations"].sort(key=lambda item: item["key"])
    return _finalize_snapshot(snapshot)


async def finish_run(
    run_id: str,
    *,
    status: Literal["succeeded", "failed", "cancelled"],
    error: Optional[Dict[str, Any]] = None,
) -> Optional[AgentRun]:
    """Finish a run once; retries cannot overwrite a terminal outcome."""
    run = await AgentRun.find_one({"run_id": run_id})
    if run is None or run.status in _TERMINAL:
        return run
    run.status = status
    run.finished_at = utc_now_iso()
    run.error = dict(error) if error else None
    await run.save()
    return run


async def export_qualification_run(
    run_id: str,
    *,
    user_id: str,
    workspace_id: str,
) -> Optional[Dict[str, Any]]:
    """Return a scoped, redacted qualification receipt for one harness run.

    The evaluator needs durable provider, timing, token, and tool-boundary
    facts.  It must never receive prompts, completions, tool arguments,
    tool results, credentials, or the full capability snapshot.  This
    projection is intentionally separate from ``Object.export`` so a newly
    added persistence field cannot become a client-visible leak by default.
    """
    run = await AgentRun.find_one({"run_id": run_id})
    if (
        run is None
        or str(getattr(run, "user_id", "")) != user_id
        or str(getattr(run, "workspace_id", "")) != workspace_id
    ):
        return None

    steps = await RunStep.find({"run_id": run_id})
    safe_steps = []
    tool_attempts: list[int] = []
    for step in steps:
        kind = str(getattr(step, "kind", "") or "")
        attempt = max(1, int(getattr(step, "attempt", 1) or 1))
        if kind == "tool":
            tool_attempts.append(attempt)
        safe_steps.append(
            {
                "step_key": str(getattr(step, "step_key", "") or ""),
                "kind": kind,
                "name": str(getattr(step, "name", "") or ""),
                "status": str(getattr(step, "status", "") or ""),
                "attempt": attempt,
                "duration_ms": getattr(step, "duration_ms", None),
                "error_code": getattr(step, "error_code", None),
                "policy_decision": str(getattr(step, "policy_decision", "") or ""),
                "denial_code": getattr(step, "denial_code", None),
                "approval_ref": getattr(step, "approval_ref", None),
                "result_class": str(getattr(step, "result_class", "") or ""),
                "snapshot_divergence": bool(
                    getattr(step, "snapshot_divergence", False)
                ),
            }
        )
    safe_steps.sort(key=lambda item: item["step_key"])

    metadata = dict(getattr(run, "metadata", None) or {})
    harness = dict(metadata.get("harness") or {})
    observability = dict(metadata.get("model_observability") or {})
    models = [
        {
            "model_id": str(item.get("model_id") or "unknown"),
            "calls": max(0, int(item.get("calls") or 0)),
            "input_tokens": max(0, int(item.get("input_tokens") or 0)),
            "output_tokens": max(0, int(item.get("output_tokens") or 0)),
            "finish_reasons": [
                str(reason) for reason in item.get("finish_reasons", [])
            ],
        }
        for item in observability.get("models", [])
        if isinstance(item, dict)
    ]
    models.sort(key=lambda item: item["model_id"])
    model_call_count = sum(int(item["calls"]) for item in models)
    status = str(getattr(run, "status", "") or "unknown")
    return {
        "run_id": str(getattr(run, "run_id", "") or ""),
        "status": status,
        "provider_configuration": {
            "provider_id": str(
                harness.get("provider_id") or getattr(run, "provider_id", "") or ""
            ),
            "provider_label": str(harness.get("provider_label") or ""),
            "agent_id": str(
                harness.get("agent_id") or getattr(run, "agent_id", "") or ""
            ),
            "capability_version": str(getattr(run, "capability_version", "") or ""),
        },
        "metrics": {
            "latency_ms": _elapsed_ms(
                str(getattr(run, "started_at", "") or ""),
                getattr(run, "finished_at", None),
            ),
            "input_tokens": max(0, int(observability.get("total_input_tokens") or 0)),
            "output_tokens": max(0, int(observability.get("total_output_tokens") or 0)),
            "peak_input_tokens": max(
                0, int(observability.get("peak_input_tokens") or 0)
            ),
            "model_call_count": model_call_count,
            "tool_call_count": len(tool_attempts),
            "tool_retries": sum(attempt - 1 for attempt in tool_attempts),
        },
        "models": models,
        "redacted_trace_ref": f"agent-run:{getattr(run, 'run_id', '')}",
        "steps": safe_steps,
    }


def _payload_fingerprint(value: Any) -> Optional[str]:
    """Hash provider payloads without retaining raw content."""
    if value is None:
        return None
    json_opts = {"default": str, "separators": (",", ":"), "sort_keys": True}
    canonical = json.dumps(value, **json_opts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def record_run_step(
    *,
    run_id: str,
    step_key: str,
    kind: str,
    name: str,
    status: RunStepStatus,
    input_value: Any = None,
    output_value: Any = None,
    error_code: Optional[str] = None,
) -> RunStep:
    """Create or advance one redacted run-step receipt idempotently.

    This records provider-observed boundaries for diagnosis. It does not claim
    durable effect delivery yet: command and environment adapters will create
    authoritative receipts before executing their own effects.
    """
    step = await RunStep.find_one({"run_id": run_id, "step_key": step_key})
    if step is None:
        step = await RunStep.create(
            run_id=run_id,
            step_key=step_key,
            kind=kind,
            name=name,
            status=status,
            input_fingerprint=_payload_fingerprint(input_value),
            output_fingerprint=_payload_fingerprint(output_value),
            error_code=error_code,
            started_at=utc_now_iso(),
            finished_at=utc_now_iso() if status != "running" else None,
        )
        return step

    if step.status != "running":
        return step
    step.status = status
    if input_value is not None:
        step.input_fingerprint = _payload_fingerprint(input_value)
    if output_value is not None:
        step.output_fingerprint = _payload_fingerprint(output_value)
    step.error_code = error_code
    if status != "running":
        step.finished_at = utc_now_iso()
    await step.save()
    return step


async def record_provider_event_step(
    run_id: str, event: Dict[str, Any], *, ordinal: int
) -> Optional[RunStep]:
    """Translate normalized provider boundary events into run-step receipts."""
    event_type = str(event.get("type") or "")
    if event_type == "tool-call":
        external_id = str(event.get("toolCallId") or ordinal)
        wire_status = str(event.get("status") or "running")
        status: RunStepStatus = (
            "failed"
            if wire_status == "error"
            else ("succeeded" if wire_status == "complete" else "running")
        )
        return await record_run_step(
            run_id=run_id,
            step_key=f"tool:{external_id}",
            kind="tool",
            name=str(event.get("name") or "tool"),
            status=status,
            input_value=event.get("args"),
            output_value=event.get("result"),
            error_code="provider_tool_error" if status == "failed" else None,
        )
    if event_type == "step":
        step = await record_run_step(
            run_id=run_id,
            step_key=f"model:{ordinal}",
            kind="model",
            name=str(event.get("modelId") or "model"),
            status="succeeded",
            output_value=event.get("usage"),
        )
        await _record_model_observability(run_id, event)
        return step
    if event_type == "final-content":
        await _record_provider_trace(run_id, event)
    return None


async def _record_model_observability(run_id: str, event: Dict[str, Any]) -> None:
    """Accumulate a compact, redacted model-use summary on the owning run.

    A ``RunStep`` preserves an immutable receipt for each provider model call.
    The run-level summary makes a whole turn diagnosable without hydrating all
    steps or retaining prompts, completions, credentials, or vendor payloads.
    It intentionally records only the model identifier observed at runtime,
    token totals, finish reason, and the already-known harness binding.
    """
    usage = event.get("usage")
    if not isinstance(usage, dict):
        usage = {}

    def _non_negative_int(value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    input_tokens = _non_negative_int(usage.get("inputTokens"))
    output_tokens = _non_negative_int(usage.get("outputTokens"))
    model_id = str(event.get("modelId") or "unknown")
    finish_reason = str(event.get("finishReason") or "unknown")

    run = await AgentRun.find_one({"run_id": run_id})
    if run is None:
        return
    metadata = dict(getattr(run, "metadata", None) or {})
    summary = dict(metadata.get("model_observability") or {})
    models = [
        dict(item) for item in summary.get("models", []) if isinstance(item, dict)
    ]
    model = next((item for item in models if item.get("model_id") == model_id), None)
    if model is None:
        model = {
            "model_id": model_id,
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "finish_reasons": [],
        }
        models.append(model)
    model["calls"] = _non_negative_int(model.get("calls")) + 1
    model["input_tokens"] = _non_negative_int(model.get("input_tokens")) + input_tokens
    model["output_tokens"] = (
        _non_negative_int(model.get("output_tokens")) + output_tokens
    )
    peak_input = _non_negative_int(summary.get("peak_input_tokens"))
    if input_tokens > peak_input:
        peak_input = input_tokens
    reasons = [str(reason) for reason in model.get("finish_reasons", [])]
    if finish_reason not in reasons:
        reasons.append(finish_reason)
    model["finish_reasons"] = reasons
    models.sort(key=lambda item: str(item.get("model_id") or ""))

    summary.update(
        {
            "version": _OBSERVABILITY_VERSION,
            "models": models,
            "peak_input_tokens": peak_input,
            "total_input_tokens": sum(
                _non_negative_int(item.get("input_tokens")) for item in models
            ),
            "total_output_tokens": sum(
                _non_negative_int(item.get("output_tokens")) for item in models
            ),
        }
    )
    metadata["model_observability"] = summary
    run.metadata = metadata
    await run.save()


async def _record_provider_trace(run_id: str, event: Dict[str, Any]) -> None:
    """Persist a bounded diagnostic trace carried by a terminal provider event.

    The harness payload may contain prompts, tool observations, or raw provider
    diagnostics. Only the stable orchestration fields that explain a failure
    or loop outcome survive here. The full payload remains provider-private.
    """
    from app.services.agent_trace import extract_turn_trace

    trace = extract_turn_trace(event)
    if trace is None:
        return
    allowed = (
        "tool_protocol",
        "protocol_reason",
        "tick_count",
        "budget",
        "ticks_light",
        "ticks_heavy",
        "model_calls",
        "guards",
        "ended_via",
        "tools_invoked",
        "skills_used",
        "context_trims",
        "fallbacks_used",
        "loop_duration_ms",
    )
    safe_trace = {key: trace[key] for key in allowed if key in trace}
    if not safe_trace:
        return
    run = await AgentRun.find_one({"run_id": run_id})
    if run is None:
        return
    metadata = dict(getattr(run, "metadata", None) or {})
    metadata["provider_trace"] = safe_trace
    run.metadata = metadata
    await run.save()


async def mint_surface_run(
    *,
    user_id: str,
    workspace_id: str,
    origin: str,
    app_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    idempotency_key: Optional[str] = None,
    capability_key: str = "",
    source: str = "core",
) -> AgentRun:
    """Mint or deterministically reuse an identity-bound short-lived run."""
    surface_key = str(idempotency_key or "").strip()
    run_id = None
    run_metadata = dict(metadata or {})
    if surface_key:
        identity = json.dumps(
            {
                "user_id": user_id,
                "workspace_id": workspace_id,
                "origin": origin,
                "app_id": app_id,
                "capability_key": capability_key,
                "source": source,
                "idempotency_key": surface_key,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        run_id = "surface:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
        existing = await AgentRun.find_one({"run_id": run_id})
        if (
            existing is not None
            and str(existing.user_id) == user_id
            and str(existing.workspace_id) == workspace_id
            and str(existing.origin) == origin
            and (existing.app_id or None) == (app_id or None)
        ):
            return existing
        run_metadata["surface_idempotency_fingerprint"] = hashlib.sha256(
            surface_key.encode("utf-8")
        ).hexdigest()
    return await start_run(
        thread_id="",
        user_id=user_id,
        workspace_id=workspace_id,
        provider_id="integral",
        origin=origin,
        app_id=app_id,
        metadata=run_metadata,
        run_id=run_id,
    )
