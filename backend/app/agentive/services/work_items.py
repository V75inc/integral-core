"""Deterministic WorkItem enqueue and legal state transitions."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional

from app.agentive.work_models import WorkItem
from app.schemas.agentive.work import (
    LEGAL_WORK_TRANSITIONS,
    EnqueueWorkRequest,
    RetryPolicy,
    WorkError,
    WorkKind,
    WorkStatus,
)
from app.utils.time import utc_now_iso


def work_item_object_id(
    *,
    kind: str,
    origin: str,
    principal_id: str,
    workspace_id: str,
    idempotency_key: str,
) -> str:
    """Deterministic Object id for a WorkItem identity tuple."""
    canonical = json.dumps(
        [kind, origin, principal_id, workspace_id, idempotency_key],
        separators=(",", ":"),
        sort_keys=False,
    )
    return "o.WorkItem." + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def input_fingerprint(payload: Dict[str, Any]) -> str:
    """Stable fingerprint of immutable enqueue input."""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


async def enqueue_work_item(
    *,
    kind: WorkKind,
    origin: str,
    principal_id: str,
    workspace_id: str,
    idempotency_key: str,
    input_payload: Optional[Dict[str, Any]] = None,
    thread_id: Optional[str] = None,
    app_id: Optional[str] = None,
    parent_work_item_id: Optional[str] = None,
    causation_id: Optional[str] = None,
    deadline_at: Optional[str] = None,
    retry_policy: Optional[RetryPolicy] = None,
) -> WorkItem:
    """Create or reuse a WorkItem by deterministic identity.

    Task 1 persists the WorkItem only. Task 2 adds the atomic initial outbox
    fact on Postgres.
    """
    req = EnqueueWorkRequest(
        kind=kind,
        origin=origin,
        principal_id=principal_id,
        workspace_id=workspace_id,
        idempotency_key=idempotency_key,
        input_payload=dict(input_payload or {}),
        thread_id=thread_id,
        app_id=app_id,
        parent_work_item_id=parent_work_item_id,
        causation_id=causation_id,
        deadline_at=deadline_at,
        retry_policy=retry_policy or RetryPolicy(),
    )
    object_id = work_item_object_id(
        kind=req.kind,
        origin=req.origin,
        principal_id=req.principal_id,
        workspace_id=req.workspace_id,
        idempotency_key=req.idempotency_key,
    )
    fingerprint = input_fingerprint(req.input_payload)
    now = utc_now_iso()
    work_item_id = object_id.removeprefix("o.WorkItem.")
    record, created = await WorkItem.create_if_absent(
        id=object_id,
        work_item_id=work_item_id,
        kind=req.kind,
        origin=req.origin,
        principal_id=req.principal_id,
        workspace_id=req.workspace_id,
        thread_id=req.thread_id or "",
        app_id=req.app_id or "",
        parent_work_item_id=req.parent_work_item_id or "",
        causation_id=req.causation_id or "",
        idempotency_key=req.idempotency_key,
        input_payload=req.input_payload,
        input_fingerprint=fingerprint,
        status="queued",
        attempt=0,
        retry_policy=req.retry_policy.model_dump(),
        next_attempt_at=now,
        deadline_at=req.deadline_at or "",
        transition_seq=0,
        created_at=now,
        updated_at=now,
    )
    if not created:
        if (
            record.input_fingerprint != fingerprint
            or record.kind != req.kind
            or record.origin != req.origin
            or record.principal_id != req.principal_id
            or record.workspace_id != req.workspace_id
        ):
            raise WorkError(
                "work.idempotency_conflict",
                "idempotency key reused with different work identity or input",
            )
    return record


async def transition_work_item(
    work_item_id: str,
    *,
    expected_status: WorkStatus,
    target: WorkStatus,
    fields: Optional[Dict[str, Any]] = None,
) -> WorkItem:
    """Apply one legal status transition to a WorkItem.

    Task 1 uses load/save. Lease-authoritative CAS lands in Task 3.
    """
    allowed = LEGAL_WORK_TRANSITIONS.get(expected_status, frozenset())
    if target not in allowed:
        raise WorkError(
            "work.invalid_transition",
            f"cannot transition {expected_status} -> {target}",
        )

    object_id = (
        work_item_id
        if work_item_id.startswith("o.WorkItem.")
        else f"o.WorkItem.{work_item_id}"
    )
    item = await WorkItem.get(object_id)
    if item is None:
        raise WorkError("work.not_found", f"work item {work_item_id} not found")
    if item.status != expected_status:
        raise WorkError(
            "work.invalid_transition",
            f"expected status {expected_status}, found {item.status}",
        )

    now = utc_now_iso()
    item.status = target
    item.transition_seq = int(item.transition_seq or 0) + 1
    item.updated_at = now
    for key, value in (fields or {}).items():
        setattr(item, key, value)
    await item.save()
    return item


__all__ = [
    "enqueue_work_item",
    "input_fingerprint",
    "transition_work_item",
    "work_item_object_id",
]
