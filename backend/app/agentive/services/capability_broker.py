"""Core-owned capability broker — snapshot gate, receipt, then adapter."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Dict, Optional

from app.agentive.services.capability_adapters import AdapterError
from app.agentive.services.execution_runs import (
    AgentRun,
    RunStep,
    build_capability_snapshot,
    finish_run,
    mint_surface_run,
)
from app.schemas.capability_broker import (
    ERR_ADAPTER,
    ERR_AMBIGUOUS_DECLARATION,
    ERR_IDENTITY_MISMATCH,
    ERR_IN_PROGRESS,
    ERR_NOT_IN_SNAPSHOT,
    ERR_REVOKED,
    ERR_RUN_NOT_FOUND,
    ERR_RUN_TERMINAL,
    ERR_UPGRADED,
    ERR_WORKSPACE_MISMATCH,
    SHORT_LIVED_ORIGINS,
    CapabilityInvocation,
    CapabilityOrigin,
    CapabilityResult,
    CapabilitySource,
    ReceiptRef,
)
from app.utils.time import utc_now_iso

_STEP_TERMINAL = frozenset(
    {"succeeded", "failed", "cancelled", "denied", "waiting_for_human"}
)
_REPLAY_RESULT_UNAVAILABLE = {"replayed": True, "result_unavailable": True}
_REPLAY_RESULT_UNAVAILABLE_MESSAGE = (
    "Prior result payload is not retained; capability was not executed again."
)


def _fingerprint(value: Any) -> Optional[str]:
    if value is None:
        return None
    canonical = json.dumps(value, default=str, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def derive_idempotency_key(inv: CapabilityInvocation) -> str:
    """Stable per-run capability call identity."""
    payload = {
        "run_id": inv.run_id,
        "capability_key": inv.capability_key,
        "source": inv.source,
        "app_id": inv.app_id,
        "connector_id": inv.connector_id,
        "arguments": inv.arguments,
    }
    canonical = json.dumps(payload, default=str, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def resolve_from_snapshot(
    snapshot: Dict[str, Any], inv: CapabilityInvocation
) -> Optional[Dict[str, Any]]:
    """Return the declaration stored on the run, or None if absent."""
    key = inv.capability_key
    if inv.source == "core":
        for item in snapshot.get("capabilities") or []:
            if isinstance(item, dict) and item.get("name") == key:
                return item
        return None
    if inv.source == "app":
        for app in snapshot.get("apps") or []:
            if not isinstance(app, dict):
                continue
            if str(app.get("app_id") or "") != str(inv.app_id or ""):
                continue
            if inv.op_class != "read":
                for operation in app.get("operations") or []:
                    if isinstance(operation, dict) and operation.get("key") == key:
                        return {
                            **operation,
                            "package_version": app.get("package_version"),
                        }
            if inv.op_class == "read":
                for query in app.get("queries") or []:
                    if isinstance(query, dict) and query.get("key") == key:
                        return query
        return None
    for env in snapshot.get("environments") or []:
        if not isinstance(env, dict):
            continue
        connector_id = str(env.get("connector_id") or "")
        if inv.connector_id and connector_id != str(inv.connector_id):
            continue
        tool_keys = env.get("tool_keys") or []
        capabilities = env.get("capabilities") or []
        if key in tool_keys or key in capabilities or connector_id == key:
            return env
        if inv.connector_id and connector_id == str(inv.connector_id):
            return env
    return None


def _kind_for(inv: CapabilityInvocation) -> str:
    if inv.op_class == "propose":
        return "approval"
    if inv.source == "connector":
        return "environment"
    if inv.op_class == "read":
        return "query"
    return "command"


def _receipt_ref(step: Any) -> ReceiptRef:
    return ReceiptRef(
        run_id=str(getattr(step, "run_id", "") or ""),
        step_key=str(getattr(step, "step_key", "") or ""),
        idempotency_key=str(getattr(step, "idempotency_key", "") or ""),
        status=str(getattr(step, "status", "") or ""),
        capability_key=str(getattr(step, "capability_key", "") or ""),
        origin=str(getattr(step, "origin", "") or ""),
        snapshot_fingerprint=str(getattr(step, "snapshot_fingerprint", "") or ""),
    )


def _result_from_step(
    step: Any,
    *,
    ok: bool,
    error_code: str = "",
    message: str = "",
    data: Any = None,
    policy: str = "",
    snapshot_fingerprint: str = "",
    snapshot_divergence: bool = False,
    replayed: bool = False,
) -> CapabilityResult:
    stored = getattr(step, "result_json", "") or ""
    if replayed and stored:
        try:
            parsed = json.loads(stored)
            data = parsed.get("data", data)
            ok = bool(parsed.get("ok", ok))
            error_code = str(parsed.get("error_code") or error_code)
            message = str(parsed.get("message") or message)
            policy = str(parsed.get("policy_decision") or policy)
            snapshot_divergence = bool(
                parsed.get("snapshot_divergence", snapshot_divergence)
            )
        except json.JSONDecodeError:
            pass
    if replayed and ok and data is None:
        data = dict(_REPLAY_RESULT_UNAVAILABLE)
        message = message or _REPLAY_RESULT_UNAVAILABLE_MESSAGE
    return CapabilityResult(
        ok=ok,
        error_code=error_code,
        message=message,
        data=data,
        receipt=_receipt_ref(step) if step is not None else None,
        policy_decision=policy or str(getattr(step, "policy_decision", "") or ""),
        snapshot_fingerprint=snapshot_fingerprint
        or str(getattr(step, "snapshot_fingerprint", "") or ""),
        snapshot_divergence=snapshot_divergence
        or bool(getattr(step, "snapshot_divergence", False)),
        replayed=replayed,
    )


def _deny(
    *,
    error_code: str,
    message: str,
    snapshot_fingerprint: str = "",
    snapshot_divergence: bool = False,
    step: Any = None,
) -> CapabilityResult:
    return CapabilityResult(
        ok=False,
        error_code=error_code,
        message=message,
        receipt=_receipt_ref(step) if step is not None else None,
        policy_decision="deny",
        snapshot_fingerprint=snapshot_fingerprint,
        snapshot_divergence=snapshot_divergence,
    )


async def _call_adapter(inv: CapabilityInvocation, cap: Dict[str, Any]) -> Any:
    from app.agentive.services.capability_adapters import dispatch_capability

    return await dispatch_capability(inv, cap)


async def _persist_envelope(step: Any, result: CapabilityResult) -> None:
    receipt_payload = {
        "ok": result.ok,
        "error_code": result.error_code,
        "policy_decision": result.policy_decision,
        "snapshot_divergence": result.snapshot_divergence,
    }
    step.result_json = json.dumps(receipt_payload, default=str, separators=(",", ":"))
    step.output_fingerprint = _fingerprint(
        result.data
        if result.ok
        else {"error_code": result.error_code, "message": result.message}
    )
    await step.save()


async def invoke(inv: CapabilityInvocation) -> CapabilityResult:
    """Authorize, receipt, then execute one declared capability."""
    started = time.perf_counter()
    run = await AgentRun.find_one({"run_id": inv.run_id})
    if run is None:
        return _deny(error_code=ERR_RUN_NOT_FOUND, message="run not found")
    if str(run.user_id) != inv.principal_id:
        return _deny(
            error_code=ERR_IDENTITY_MISMATCH,
            message="principal does not match run",
        )
    if str(run.workspace_id) != inv.workspace_id:
        return _deny(
            error_code=ERR_WORKSPACE_MISMATCH,
            message="workspace does not match run",
        )
    run_is_terminal = str(run.status) in {"succeeded", "failed", "cancelled"}

    snapshot = dict(getattr(run, "capability_snapshot", None) or {})
    snapshot_fp = str(snapshot.get("fingerprint") or "")
    cap = resolve_from_snapshot(snapshot, inv)
    if cap is None:
        return _deny(
            error_code=ERR_NOT_IN_SNAPSHOT,
            message="unknown capability is not in the run snapshot",
            snapshot_fingerprint=snapshot_fp,
        )

    current = await build_capability_snapshot(inv.workspace_id)
    current_fp = str(current.get("fingerprint") or "")
    divergence = bool(snapshot_fp and current_fp and snapshot_fp != current_fp)
    current_cap = resolve_from_snapshot(current, inv)

    if inv.op_class in ("propose", "execute"):
        if current_cap is None:
            return _deny(
                error_code=ERR_REVOKED,
                message="capability is no longer granted",
                snapshot_fingerprint=snapshot_fp,
                snapshot_divergence=divergence,
            )
        if divergence:
            return _deny(
                error_code=ERR_UPGRADED,
                message="capability snapshot changed; reauthorization required",
                snapshot_fingerprint=snapshot_fp,
                snapshot_divergence=True,
            )
    elif current_cap is None:
        return _deny(
            error_code=ERR_REVOKED,
            message="capability is no longer granted",
            snapshot_fingerprint=snapshot_fp,
            snapshot_divergence=divergence,
        )

    idem_key = (inv.idempotency_key or "").strip() or derive_idempotency_key(inv)
    inv.idempotency_key = idem_key
    step = await RunStep.find_one({"run_id": inv.run_id, "idempotency_key": idem_key})
    if step is not None and str(step.status) in _STEP_TERMINAL:
        if (
            inv.capability_key == "integral_query_spec"
            or isinstance(cap.get("query_template"), dict)
        ) and str(step.status) == "succeeded":
            try:
                data = await _call_adapter(inv, cap)
            except AdapterError as exc:
                return _result_from_step(
                    step,
                    ok=False,
                    error_code=exc.error_code,
                    message=exc.message,
                    replayed=True,
                    snapshot_fingerprint=snapshot_fp,
                )
            result = _result_from_step(
                step,
                ok=True,
                data=data,
                replayed=True,
                snapshot_fingerprint=snapshot_fp,
                snapshot_divergence=bool(getattr(step, "snapshot_divergence", False)),
            )
            if isinstance(result.data, dict) and result.receipt is not None:
                result.data["receipt"] = result.receipt.model_dump()
            return result
        return _result_from_step(
            step,
            ok=str(step.status) in {"succeeded", "waiting_for_human"},
            error_code=str(
                getattr(step, "denial_code", None)
                or getattr(step, "error_code", None)
                or ""
            ),
            replayed=True,
            snapshot_fingerprint=snapshot_fp,
            snapshot_divergence=bool(getattr(step, "snapshot_divergence", False)),
        )
    if run_is_terminal:
        return _deny(
            error_code=ERR_RUN_TERMINAL,
            message="run is already terminal",
            snapshot_fingerprint=snapshot_fp,
        )
    if step is not None and str(step.status) == "running":
        return _deny(
            error_code=ERR_IN_PROGRESS,
            message="capability invocation already in progress",
            snapshot_fingerprint=snapshot_fp,
            step=step,
        )

    step_key = f"capability:{idem_key[:16]}"
    step = await RunStep.create(
        run_id=inv.run_id,
        step_key=step_key,
        kind=_kind_for(inv),
        name=inv.capability_key,
        status="running",
        input_fingerprint=_fingerprint(inv.arguments),
        capability_key=inv.capability_key,
        capability_version=str(
            cap.get("package_version") or snapshot.get("manifest_version") or ""
        ),
        capability_source=inv.source,
        app_id=inv.app_id,
        connector_id=inv.connector_id,
        origin=inv.origin,
        principal_id=inv.principal_id,
        workspace_id=inv.workspace_id,
        idempotency_key=idem_key,
        attempt=1,
        policy_decision="allow",
        snapshot_fingerprint=snapshot_fp,
        snapshot_divergence=divergence,
        started_at=utc_now_iso(),
    )

    try:
        data = await _call_adapter(inv, cap)
    except AdapterError as exc:
        step.status = "failed"
        step.error_code = ERR_ADAPTER
        step.adapter_error_code = exc.error_code
        step.denial_code = exc.error_code
        step.result_class = "failed"
        step.finished_at = utc_now_iso()
        step.duration_ms = (time.perf_counter() - started) * 1000.0
        result = _result_from_step(
            step,
            ok=False,
            error_code=exc.error_code,
            message=exc.message,
            policy="allow",
            snapshot_fingerprint=snapshot_fp,
            snapshot_divergence=divergence,
        )
        await _persist_envelope(step, result)
        if inv.origin in SHORT_LIVED_ORIGINS:
            await finish_run(
                inv.run_id,
                status="failed",
                error={"error_code": exc.error_code, "message": exc.message},
            )
        return result
    except Exception as exc:  # noqa: BLE001
        step.status = "failed"
        step.error_code = ERR_ADAPTER
        step.adapter_error_code = type(exc).__name__
        step.result_class = "failed"
        step.finished_at = utc_now_iso()
        step.duration_ms = (time.perf_counter() - started) * 1000.0
        result = _result_from_step(
            step,
            ok=False,
            error_code=ERR_ADAPTER,
            message="capability adapter failed",
            policy="allow",
            snapshot_fingerprint=snapshot_fp,
            snapshot_divergence=divergence,
        )
        await _persist_envelope(step, result)
        if inv.origin in SHORT_LIVED_ORIGINS:
            await finish_run(
                inv.run_id,
                status="failed",
                error={"error_code": ERR_ADAPTER},
            )
        return result

    approval_ref = None
    status = "succeeded"
    if isinstance(data, dict) and data.get("_kind") == "staged_change":
        approval_ref = str(data.get("token") or "") or None
        status = "waiting_for_human"
    step.status = status
    step.approval_ref = approval_ref
    step.result_class = status
    step.finished_at = utc_now_iso()
    step.duration_ms = (time.perf_counter() - started) * 1000.0
    result = _result_from_step(
        step,
        ok=True,
        data=data,
        policy="allow",
        snapshot_fingerprint=snapshot_fp,
        snapshot_divergence=divergence,
    )
    if (
        (
            inv.capability_key == "integral_query_spec"
            or isinstance(cap.get("query_template"), dict)
        )
        and isinstance(result.data, dict)
        and result.receipt is not None
    ):
        result.data["receipt"] = result.receipt.model_dump()
    await _persist_envelope(step, result)
    if inv.origin in SHORT_LIVED_ORIGINS:
        await finish_run(inv.run_id, status="succeeded")
    return result


def infer_source_and_op_class(
    capability_key: str, *, app_id: Optional[str] = None
) -> tuple[CapabilitySource, str]:
    """Best-effort source/op_class from the live catalogue (not the snapshot)."""
    if app_id:
        return "app", "execute"
    if capability_key.startswith("mcp__"):
        return "connector", "execute"
    from app.agentive.tooling.catalogue import build_tool_catalogue

    for item in build_tool_catalogue():
        if item.get("name") == capability_key:
            return "core", str(item.get("op_class") or "read")
    return "core", "read"


async def invoke_declared_capability(
    *,
    principal_id: str,
    workspace_id: str,
    capability_key: str,
    origin: CapabilityOrigin,
    source: CapabilitySource = "core",
    op_class: str = "read",
    arguments: Optional[Dict[str, Any]] = None,
    run_id: Optional[str] = None,
    app_id: Optional[str] = None,
    connector_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    session_id: Optional[str] = None,
    interaction_id: Optional[str] = None,
    skill_tools_required: Optional[list] = None,
) -> CapabilityResult:
    """Mint a short-lived run when the caller has none, then invoke."""
    if not run_id:
        run = await mint_surface_run(
            user_id=principal_id,
            workspace_id=workspace_id or "",
            origin=origin,
            app_id=app_id,
            idempotency_key=idempotency_key,
            capability_key=capability_key,
            source=source,
        )
        run_id = run.run_id
    if source == "core" and capability_key == "integral_invoke_app_operation":
        run = await AgentRun.find_one({"run_id": run_id})
        if (
            run is not None
            and str(run.user_id) == principal_id
            and str(run.workspace_id) == (workspace_id or "")
        ):
            outer_arguments = dict(arguments or {})
            target_app_id = str(outer_arguments.get("app_id") or "")
            target_key = str(outer_arguments.get("operation_key") or "")
            snapshot = dict(getattr(run, "capability_snapshot", None) or {})
            app_snapshot: Dict[str, Any] = next(
                (
                    item
                    for item in snapshot.get("apps") or []
                    if isinstance(item, dict)
                    and str(item.get("app_id") or "") == target_app_id
                ),
                {},
            )
            operation_match = any(
                isinstance(operation, dict)
                and str(operation.get("key") or "") == target_key
                for operation in app_snapshot.get("operations") or []
            )
            query_match = any(
                isinstance(query, dict) and str(query.get("key") or "") == target_key
                for query in app_snapshot.get("queries") or []
            )
            if operation_match and query_match:
                return _deny(
                    error_code=ERR_AMBIGUOUS_DECLARATION,
                    message="App capability key is ambiguous in run snapshot",
                )
            if query_match:
                capability_key = target_key
                source = "app"
                op_class = "read"
                app_id = target_app_id
                arguments = dict(outer_arguments.get("input") or {})
    if op_class not in ("read", "propose", "execute"):
        op_class = "execute"
    inv = CapabilityInvocation(
        run_id=run_id,
        principal_id=principal_id,
        workspace_id=workspace_id or "",
        origin=origin,
        capability_key=capability_key,
        source=source,
        op_class=op_class,  # type: ignore[arg-type]
        arguments=dict(arguments or {}),
        idempotency_key=idempotency_key,
        app_id=app_id,
        connector_id=connector_id,
        session_id=session_id,
        interaction_id=interaction_id,
        skill_tools_required=skill_tools_required,
    )
    return await invoke(inv)
