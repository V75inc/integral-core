"""Deterministic WorkItem enqueue, transitions, and lease authority."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from app.agentive.work_models import WorkItem
from app.schemas.agentive.work import (
    RetryPolicy,
    WorkError,
    WorkKind,
    WorkStatus,
)
from app.utils.time import utc_now_iso

DEFAULT_LEASE_SECONDS = 30.0
TOPIC_LEASE_RECLAIMED = "work.lease_reclaimed"


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


def recommended_heartbeat_interval(lease_seconds: float) -> float:
    """Heartbeat must run at an interval no greater than lease/3."""
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be > 0")
    return float(lease_seconds) / 3.0


def _object_id(work_item_id: str) -> str:
    if work_item_id.startswith("o.WorkItem."):
        return work_item_id
    return f"o.WorkItem.{work_item_id}"


def _bare_id(work_item_id: str) -> str:
    return _object_id(work_item_id).removeprefix("o.WorkItem.")


def _parse_iso(value: str) -> Optional[datetime]:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _lease_expiry(lease_seconds: float, *, now: Optional[datetime] = None) -> str:
    base = now or datetime.now(timezone.utc)
    return (base + timedelta(seconds=float(lease_seconds))).isoformat()


def _is_due(item: WorkItem, now: datetime) -> bool:
    if item.status not in ("queued", "retry_wait"):
        return False
    nxt = _parse_iso(item.next_attempt_at)
    if nxt is None:
        return True
    if nxt.tzinfo is None:
        nxt = nxt.replace(tzinfo=timezone.utc)
    return nxt <= now


def _lease_expired(item: WorkItem, now: datetime) -> bool:
    exp = _parse_iso(item.lease_expires_at)
    if exp is None:
        return True
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return exp <= now


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


async def claim_due_candidate(
    *,
    worker_id: str,
    lease_seconds: float = DEFAULT_LEASE_SECONDS,
    work_item_id: Optional[str] = None,
    transaction: Any = None,
) -> Optional[WorkItem]:
    """Claim one due queued/retry_wait WorkItem into ``running`` under a lease."""
    from app.agentive.services.work_outbox import TOPIC_TRANSITIONED, cas_work_item_update

    now_dt = datetime.now(timezone.utc)
    expires = _lease_expiry(lease_seconds, now=now_dt)
    token = secrets.token_urlsafe(16)

    async def _try_claim(candidate: WorkItem) -> Optional[WorkItem]:
        if not _is_due(candidate, now_dt):
            return None
        from_status = candidate.status
        try:
            return await cas_work_item_update(
                candidate.work_item_id,
                expected={"status": from_status},
                updates={
                    "status": "running",
                    "attempt": int(candidate.attempt or 0) + 1,
                    "lease_owner": worker_id,
                    "lease_token": token,
                    "lease_fence": int(candidate.lease_fence or 0) + 1,
                    "lease_expires_at": expires,
                },
                outbox_topic=TOPIC_TRANSITIONED,
                outbox_payload={
                    "from_status": from_status,
                    "to_status": "running",
                    "lease_owner": worker_id,
                },
                bump_transition_seq=True,
                error_code="work.cas_conflict",
                transaction=transaction,
            )
        except WorkError as exc:
            if exc.code in ("work.cas_conflict", "work.lease_lost"):
                return None
            raise

    if work_item_id:
        item = await WorkItem.get(_object_id(work_item_id))
        if item is None:
            return None
        return await _try_claim(item)

    for status in ("queued", "retry_wait"):
        candidates = list(await WorkItem.find({"context.status": status}, limit=50))
        for candidate in candidates:
            if not _is_due(candidate, now_dt):
                continue
            won = await _try_claim(candidate)
            if won is not None:
                return won
    return None


async def heartbeat_lease(
    work_item_id: str,
    *,
    lease_token: str,
    lease_fence: int,
    worker_id: str,
    lease_seconds: float = DEFAULT_LEASE_SECONDS,
    transaction: Any = None,
) -> WorkItem:
    """Extend lease expiry when token and fence still match."""
    from app.agentive.services.work_outbox import cas_work_item_update

    expires = _lease_expiry(lease_seconds)
    return await cas_work_item_update(
        work_item_id,
        expected={
            "status": "running",
            "lease_token": lease_token,
            "lease_fence": int(lease_fence),
            "lease_owner": worker_id,
        },
        updates={"lease_expires_at": expires},
        error_code="work.lease_lost",
        transaction=transaction,
    )


async def transition_leased(
    work_item_id: str,
    *,
    lease_token: str,
    lease_fence: int,
    expected_status: WorkStatus,
    target: WorkStatus,
    fields: Optional[Dict[str, Any]] = None,
    transaction: Any = None,
) -> WorkItem:
    """Status transition authorized only by the current lease token/fence."""
    from app.agentive.services.work_outbox import (
        TOPIC_TRANSITIONED,
        cas_work_item_update,
    )
    from app.schemas.agentive.work import LEGAL_WORK_TRANSITIONS

    allowed = LEGAL_WORK_TRANSITIONS.get(expected_status, frozenset())
    if target not in allowed:
        raise WorkError(
            "work.invalid_transition",
            f"cannot transition {expected_status} -> {target}",
        )
    updates: Dict[str, Any] = {"status": target}
    updates.update(fields or {})
    # Clear lease on terminal / wait states that leave running.
    if target != "running":
        updates.setdefault("lease_token", "")
        updates.setdefault("lease_owner", "")
        updates.setdefault("lease_expires_at", "")
    return await cas_work_item_update(
        work_item_id,
        expected={
            "status": expected_status,
            "lease_token": lease_token,
            "lease_fence": int(lease_fence),
        },
        updates=updates,
        outbox_topic=TOPIC_TRANSITIONED,
        outbox_payload={
            "from_status": expected_status,
            "to_status": target,
        },
        bump_transition_seq=True,
        error_code="work.lease_lost",
        transaction=transaction,
    )


async def reclaim_expired_lease(
    work_item_id: str,
    *,
    worker_id: str,
    lease_seconds: float = DEFAULT_LEASE_SECONDS,
    transaction: Any = None,
) -> WorkItem:
    """Reclaim an expired running lease with a new token and incremented fence."""
    from app.agentive.services.work_outbox import cas_work_item_update

    now_dt = datetime.now(timezone.utc)
    object_id = _object_id(work_item_id)
    item = await WorkItem.get(object_id)
    if item is None:
        raise WorkError("work.not_found", f"work item {work_item_id} not found")
    if item.status != "running":
        raise WorkError(
            "work.invalid_transition",
            f"cannot reclaim lease in status {item.status}",
        )
    if not _lease_expired(item, now_dt):
        raise WorkError("work.lease_active", "lease has not expired")
    new_token = secrets.token_urlsafe(16)
    new_fence = int(item.lease_fence or 0) + 1
    return await cas_work_item_update(
        item.work_item_id,
        expected={
            "status": "running",
            "lease_token": item.lease_token,
            "lease_fence": int(item.lease_fence or 0),
        },
        updates={
            "lease_owner": worker_id,
            "lease_token": new_token,
            "lease_fence": new_fence,
            "lease_expires_at": _lease_expiry(lease_seconds, now=now_dt),
        },
        outbox_topic=TOPIC_LEASE_RECLAIMED,
        outbox_payload={
            "previous_fence": int(item.lease_fence or 0),
            "lease_fence": new_fence,
            "lease_owner": worker_id,
        },
        bump_transition_seq=True,
        error_code="work.lease_lost",
        transaction=transaction,
    )

async def force_expire_lease_for_tests(work_item_id: str) -> WorkItem:
    """Test helper: set lease_expires_at to the past without touching the token."""
    from app.agentive.services.work_outbox import cas_work_item_update

    past = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    item = await WorkItem.get(_object_id(work_item_id))
    if item is None:
        raise WorkError("work.not_found", f"work item {work_item_id} not found")
    return await cas_work_item_update(
        item.work_item_id,
        expected={
            "status": item.status,
            "lease_token": item.lease_token,
            "lease_fence": int(item.lease_fence or 0),
        },
        updates={"lease_expires_at": past},
        error_code="work.cas_conflict",
    )


__all__ = [
    "DEFAULT_LEASE_SECONDS",
    "claim_due_candidate",
    "enqueue_work_item",
    "force_expire_lease_for_tests",
    "heartbeat_lease",
    "input_fingerprint",
    "reclaim_expired_lease",
    "recommended_heartbeat_interval",
    "transition_leased",
    "transition_work_item",
    "work_item_object_id",
]
