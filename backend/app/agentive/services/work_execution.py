"""WorkExecutionContext helpers and effect-boundary gates (Task 5)."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.agentive.work_models import WorkItem
from app.schemas.agentive.work import WorkError, WorkExecutionContext


def deterministic_run_id(*, work_item_id: str, attempt: int) -> str:
    """``workrun:{sha256(work_item_id:attempt)}``."""
    digest = hashlib.sha256(
        f"{work_item_id}:{int(attempt)}".encode("utf-8")
    ).hexdigest()
    return f"workrun:{digest}"


def effect_key(*, work_item_id: str, logical_step_key: str) -> str:
    """``sha256(work_item_id:logical_step_key)`` — independent of attempt."""
    return hashlib.sha256(
        f"{work_item_id}:{logical_step_key}".encode("utf-8")
    ).hexdigest()


def logical_step_key_for(*, kind: str, ordinal: int = 0) -> str:
    """Stable logical step slot for a WorkItem kind."""
    if kind == "capability":
        return "capability:0"
    return f"provider:{int(ordinal)}"


def build_work_execution_context(
    *,
    work_item: WorkItem,
    logical_step_key: str,
    cancellation_signal: bool = False,
) -> WorkExecutionContext:
    """Build immutable context from a claimed WorkItem + logical step."""
    wid = work_item.work_item_id
    attempt = int(work_item.attempt or 0)
    run_id = deterministic_run_id(work_item_id=wid, attempt=attempt)
    return WorkExecutionContext(
        work_item_id=wid,
        attempt=attempt,
        run_id=run_id,
        principal_id=work_item.principal_id,
        workspace_id=work_item.workspace_id,
        logical_step_key=logical_step_key,
        effect_key=effect_key(work_item_id=wid, logical_step_key=logical_step_key),
        lease_token=work_item.lease_token or "",
        lease_fence=int(work_item.lease_fence or 0),
        deadline_at=work_item.deadline_at or None,
        cancellation_signal=bool(
            cancellation_signal or bool(work_item.cancel_requested_at)
        ),
    )


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


async def assert_effect_boundary_allowed(ctx: WorkExecutionContext) -> WorkItem:
    """Fail closed before an adapter runs when lease/deadline/cancel say stop."""
    item = await WorkItem.get(f"o.WorkItem.{ctx.work_item_id}")
    if item is None:
        raise WorkError("work.not_found", f"work item {ctx.work_item_id} not found")
    if item.status != "running":
        raise WorkError(
            "work.invalid_transition",
            f"effects require running status, found {item.status}",
        )
    if (item.lease_token or "") != ctx.lease_token or int(item.lease_fence or 0) != int(
        ctx.lease_fence
    ):
        raise WorkError("work.lease_lost", "lease token/fence mismatch")
    now = datetime.now(timezone.utc)
    exp = _parse_iso(item.lease_expires_at)
    if exp is not None:
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp <= now:
            raise WorkError("work.lease_lost", "lease expired")
    deadline = _parse_iso(ctx.deadline_at or item.deadline_at)
    if deadline is not None:
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        if deadline <= now:
            raise WorkError("work.deadline_exceeded", "deadline passed")
    if ctx.cancellation_signal or item.cancel_requested_at:
        raise WorkError("work.cancelled", "cancel requested")
    return item


def persist_logical_step_slot(
    metadata: Dict[str, Any],
    *,
    logical_step_key: str,
    capability_key: str,
    input_fingerprint: str,
) -> Dict[str, Any]:
    """Record or validate a provider ordinal slot on AgentRun.metadata.

    Raises ``work.logical_step_conflict`` when an occupied ordinal is reused
    with a different capability or input fingerprint.
    """
    slots = dict(metadata.get("logical_steps") or {})
    existing = slots.get(logical_step_key)
    if existing is not None:
        if (
            existing.get("capability_key") != capability_key
            or existing.get("input_fingerprint") != input_fingerprint
        ):
            raise WorkError(
                "work.logical_step_conflict",
                f"logical step {logical_step_key} occupied with different effect",
            )
        return metadata
    slots[logical_step_key] = {
        "capability_key": capability_key,
        "input_fingerprint": input_fingerprint,
    }
    out = dict(metadata)
    out["logical_steps"] = slots
    return out


# Adapters that may run under a WorkExecutionContext today.
_REPLAYABLE_SOURCES = frozenset({"core", "app", "connector"})


def assert_adapter_replayable(*, source: str, capability_key: str) -> None:
    """Unsupported effect surfaces fail closed before invocation."""
    if source not in _REPLAYABLE_SOURCES:
        raise WorkError(
            "work.non_replayable_effect",
            f"source {source!r} is not replayable under WorkExecutionContext",
        )
    if not (capability_key or "").strip():
        raise WorkError(
            "work.non_replayable_effect",
            "missing capability_key for work-backed effect",
        )


def context_from_mapping(
    data: Optional[Dict[str, Any]],
) -> Optional[WorkExecutionContext]:
    """Hydrate WorkExecutionContext from visitor/extra_data mapping."""
    if not data:
        return None
    # Prefer nested blob when present.
    nested = data.get("work_execution_context")
    if isinstance(nested, dict):
        try:
            return WorkExecutionContext.model_validate(nested)
        except Exception:
            return None
    required = (
        "work_item_id",
        "attempt",
        "run_id",
        "principal_id",
        "workspace_id",
        "logical_step_key",
        "effect_key",
        "lease_token",
        "lease_fence",
    )
    if not all(k in data for k in required):
        return None
    try:
        return WorkExecutionContext.model_validate(
            {k: data[k] for k in required}
            | {
                "deadline_at": data.get("deadline_at"),
                "cancellation_signal": bool(data.get("cancellation_signal")),
            }
        )
    except Exception:
        return None


__all__ = [
    "assert_adapter_replayable",
    "assert_effect_boundary_allowed",
    "build_work_execution_context",
    "context_from_mapping",
    "deterministic_run_id",
    "effect_key",
    "logical_step_key_for",
    "persist_logical_step_slot",
]
