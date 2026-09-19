"""Deterministic WorkItem enqueue and legal state transitions."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional

from app.agentive.work_models import WorkItem
from app.schemas.agentive.work import (
    RetryPolicy,
    WorkKind,
    WorkStatus,
)


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
    transaction: Any = None,
) -> WorkItem:
    """Create or reuse a WorkItem; persists the initial outbox fact atomically."""
    from app.agentive.services.work_outbox import enqueue_work_item_unit

    return await enqueue_work_item_unit(
        kind=kind,
        origin=origin,
        principal_id=principal_id,
        workspace_id=workspace_id,
        idempotency_key=idempotency_key,
        input_payload=input_payload,
        thread_id=thread_id,
        app_id=app_id,
        parent_work_item_id=parent_work_item_id,
        causation_id=causation_id,
        deadline_at=deadline_at,
        retry_policy=retry_policy,
        transaction=transaction,
    )


async def transition_work_item(
    work_item_id: str,
    *,
    expected_status: WorkStatus,
    target: WorkStatus,
    fields: Optional[Dict[str, Any]] = None,
    transaction: Any = None,
) -> WorkItem:
    """Apply one legal status transition and emit a transition outbox fact."""
    from app.agentive.services.work_outbox import transition_work_item_unit

    return await transition_work_item_unit(
        work_item_id,
        expected_status=expected_status,
        target=target,
        fields=fields,
        transaction=transaction,
    )


__all__ = [
    "enqueue_work_item",
    "input_fingerprint",
    "transition_work_item",
    "work_item_object_id",
]
