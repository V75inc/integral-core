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
    WorkFailure,
    WorkKind,
    WorkStatus,
)
from app.utils.time import utc_now_iso

DEFAULT_LEASE_SECONDS = 30.0
TOPIC_LEASE_RECLAIMED = "work.lease_reclaimed"
RETRYABLE_FAILURE_CLASSES = frozenset(
    {"transient", "rate_limited", "dependency_unavailable"}
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


def continuation_fingerprint(
    *,
    input_payload: Dict[str, Any],
    plan_revision: Optional[str] = None,
    plan: Optional[Dict[str, Any]] = None,
    dependency_work_item_ids: Optional[list[str]] = None,
    precommit_draft: Optional[Dict[str, Any]] = None,
    remaining_obligations: Optional[list[Dict[str, Any]]] = None,
    app_id: Optional[str] = None,
    definition_id: Optional[str] = None,
) -> str:
    """Fingerprint the entire immutable continuation request.

    One idempotency key names one revision-bound plan, not merely one adapter
    payload. A changed dependency or obligation must therefore fail closed.
    """
    return input_fingerprint(
        {
            "input_payload": dict(input_payload or {}),
            "plan_revision": str(plan_revision or ""),
            "plan": dict(plan or {}),
            "dependency_work_item_ids": sorted(
                str(value) for value in (dependency_work_item_ids or [])
            ),
            "precommit_draft": dict(precommit_draft or {}),
            "remaining_obligations": list(remaining_obligations or []),
            "app_id": str(app_id or ""),
            "definition_id": str(definition_id or ""),
        }
    )


async def resolve_active_definition_binding(
    *,
    app_id: str,
    workspace_id: str,
    requested_definition_id: Optional[str] = None,
) -> str:
    """Resolve the exact active contract for App-bound work.

    A retryable work request must never float with the mutable App profile.
    The active revision is captured at enqueue, constrained to the request's
    workspace, and later rechecked at the effect boundary.
    """
    from app.models.nodes import App
    from app.services.application_definitions import get_active_application_definition

    app_node = await App.get(app_id)
    if app_node is None:
        raise WorkError("work.app_not_found", f"app {app_id} not found")
    if str(getattr(app_node, "workspace_id", "") or "") != workspace_id:
        raise WorkError(
            "work.app_workspace_mismatch",
            "App does not belong to the requested workspace",
        )
    definition = await get_active_application_definition(app_node)
    if definition is None:
        raise WorkError(
            "work.definition_required",
            "App-bound work requires an active application definition",
        )
    active_definition_id = str(definition.id)
    if requested_definition_id and requested_definition_id != active_definition_id:
        raise WorkError(
            "work.definition_stale",
            "Requested definition is not the App's active definition; replan first",
        )
    return active_definition_id


def recommended_heartbeat_interval(lease_seconds: float) -> float:
    """Heartbeat must run at an interval no greater than lease/3."""
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be > 0")
    return float(lease_seconds) / 3.0


def compute_retry_delay(
    *,
    work_item_id: str,
    attempt: int,
    retry_policy: RetryPolicy,
) -> float:
    """Deterministic symmetric jitter backoff for failed attempt ``n >= 1``."""
    n = int(attempt)
    if n < 1:
        raise ValueError("attempt must be >= 1 for retry delay")
    base = min(
        retry_policy.max_delay_seconds,
        retry_policy.base_delay_seconds * (2 ** (n - 1)),
    )
    digest = hashlib.sha256(f"{work_item_id}:{n}".encode("utf-8")).digest()
    u = int.from_bytes(digest[:8], "big") / float(2**64)
    factor = (1.0 - retry_policy.jitter_ratio) + (2.0 * retry_policy.jitter_ratio * u)
    return min(retry_policy.max_delay_seconds, base * factor)


def normalize_failure(
    *,
    class_: str,
    code: str,
    message: str = "",
    retryable: Optional[bool] = None,
) -> Dict[str, Any]:
    """Build the stored failure record (`class` key)."""
    from app.schemas.agentive.work import WorkFailure

    retry = (
        bool(retryable)
        if retryable is not None
        else class_ in RETRYABLE_FAILURE_CLASSES
    )
    failure = WorkFailure(
        class_=class_,  # type: ignore[arg-type]
        code=code,
        message=message,
        retryable=retry,
    )
    return failure.model_dump(by_alias=True)


def _deadline_passed(item: WorkItem, now: datetime) -> bool:
    deadline = _parse_iso(item.deadline_at)
    if deadline is None:
        return False
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return deadline <= now


async def expire_work_item(work_item_id: str, *, reason: str = "deadline") -> WorkItem:
    """Terminalize an overdue queued/waiting item."""
    from app.agentive.services.work_outbox import (
        TOPIC_TRANSITIONED,
        cas_work_item_update,
    )

    item = await WorkItem.get(_object_id(work_item_id))
    if item is None:
        raise WorkError("work.not_found", f"work item {work_item_id} not found")
    if item.status in ("succeeded", "failed", "cancelled", "expired", "dead_letter"):
        return item
    if item.status not in (
        "queued",
        "retry_wait",
        "waiting_for_human",
        "waiting_for_event",
    ):
        raise WorkError(
            "work.invalid_transition",
            f"cannot expire from status {item.status}",
        )
    return await cas_work_item_update(
        item.work_item_id,
        expected={"status": item.status},
        updates={
            "status": "expired",
            "failure": normalize_failure(
                class_="deadline_exceeded",
                code="work.deadline_exceeded",
                message=reason,
                retryable=False,
            ),
            "lease_token": "",
            "lease_owner": "",
            "lease_expires_at": "",
        },
        outbox_topic=TOPIC_TRANSITIONED,
        outbox_payload={
            "from_status": item.status,
            "to_status": "expired",
        },
        bump_transition_seq=True,
        error_code="work.cas_conflict",
    )


async def schedule_retry(
    work_item_id: str,
    *,
    lease_token: str,
    lease_fence: int,
    failure: WorkFailure,
) -> WorkItem:
    """Move a leased running item to retry_wait or dead_letter."""
    if not isinstance(failure, WorkFailure):
        failure = WorkFailure.model_validate(failure)

    item = await WorkItem.get(_object_id(work_item_id))
    if item is None:
        raise WorkError("work.not_found", f"work item {work_item_id} not found")
    policy = RetryPolicy.model_validate(item.retry_policy or {})
    attempt = int(item.attempt or 0)
    failure_doc = failure.model_dump(by_alias=True)
    retryable = bool(failure.retryable) and failure.class_ in RETRYABLE_FAILURE_CLASSES
    if (not retryable) or attempt >= int(policy.max_attempts):
        target = "dead_letter" if retryable else "failed"
        if failure.class_ in {"permanent", "policy_denied", "non_replayable"}:
            target = "failed"
        if failure.class_ == "non_replayable":
            target = "dead_letter"
        return await transition_leased(
            work_item_id,
            lease_token=lease_token,
            lease_fence=lease_fence,
            expected_status="running",
            target=target,  # type: ignore[arg-type]
            fields={"failure": failure_doc},
        )

    delay = compute_retry_delay(
        work_item_id=item.work_item_id, attempt=attempt, retry_policy=policy
    )
    next_at = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
    return await transition_leased(
        work_item_id,
        lease_token=lease_token,
        lease_fence=lease_fence,
        expected_status="running",
        target="retry_wait",
        fields={"failure": failure_doc, "next_attempt_at": next_at},
    )


async def cancel_work_item(work_item_id: str) -> WorkItem:
    """Cancel queued/waiting immediately; request cancel for running work."""
    from app.agentive.services.work_outbox import (
        TOPIC_TRANSITIONED,
        cas_work_item_update,
    )

    item = await WorkItem.get(_object_id(work_item_id))
    if item is None:
        raise WorkError("work.not_found", f"work item {work_item_id} not found")
    if item.status in ("succeeded", "failed", "cancelled", "expired", "dead_letter"):
        return item
    now = utc_now_iso()
    if item.status == "running":
        return await cas_work_item_update(
            item.work_item_id,
            expected={"status": "running", "lease_token": item.lease_token},
            updates={"cancel_requested_at": now},
            error_code="work.cas_conflict",
        )
    if item.status not in (
        "queued",
        "retry_wait",
        "waiting_for_human",
        "waiting_for_event",
    ):
        raise WorkError(
            "work.invalid_transition",
            f"cannot cancel from status {item.status}",
        )
    return await cas_work_item_update(
        item.work_item_id,
        expected={"status": item.status},
        updates={
            "status": "cancelled",
            "cancel_requested_at": now,
            "failure": normalize_failure(
                class_="cancelled",
                code="work.cancelled",
                message="cancelled",
                retryable=False,
            ),
            "lease_token": "",
            "lease_owner": "",
            "lease_expires_at": "",
        },
        outbox_topic=TOPIC_TRANSITIONED,
        outbox_payload={"from_status": item.status, "to_status": "cancelled"},
        bump_transition_seq=True,
        error_code="work.cas_conflict",
    )


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


async def _dependencies_allow_claim(candidate: WorkItem) -> bool:
    """Gate a work item on successful prerequisites and record terminal gaps."""
    dependency_ids = [str(value) for value in candidate.dependency_work_item_ids]
    if not dependency_ids:
        return True
    blockers = []
    for dependency_id in dependency_ids:
        dependency = await WorkItem.get(_object_id(dependency_id))
        if dependency is None:
            blockers.append({"work_item_id": dependency_id, "status": "missing"})
        elif dependency.status != "succeeded":
            blockers.append(
                {"work_item_id": dependency.work_item_id, "status": dependency.status}
            )
    if not blockers:
        return True
    terminal = {"failed", "cancelled", "expired", "dead_letter", "missing"}
    if any(blocker["status"] in terminal for blocker in blockers):
        try:
            await transition_work_item(
                candidate.work_item_id,
                expected_status=candidate.status,
                target="failed",
                fields={
                    "failure": normalize_failure(
                        class_="permanent",
                        code="work.dependency_unmet",
                        message="a prerequisite cannot complete",
                        retryable=False,
                    ),
                    "remaining_obligations": blockers,
                },
            )
        except WorkError:
            pass
    return False


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
    plan_revision: Optional[str] = None,
    plan: Optional[Dict[str, Any]] = None,
    dependency_work_item_ids: Optional[list[str]] = None,
    precommit_draft: Optional[Dict[str, Any]] = None,
    remaining_obligations: Optional[list[Dict[str, Any]]] = None,
    thread_id: Optional[str] = None,
    app_id: Optional[str] = None,
    definition_id: Optional[str] = None,
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
        plan_revision=plan_revision,
        plan=plan,
        dependency_work_item_ids=dependency_work_item_ids,
        precommit_draft=precommit_draft,
        remaining_obligations=remaining_obligations,
        thread_id=thread_id,
        app_id=app_id,
        definition_id=definition_id,
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
    from app.agentive.services.work_outbox import (
        TOPIC_TRANSITIONED,
        cas_work_item_update,
    )

    now_dt = datetime.now(timezone.utc)
    expires = _lease_expiry(lease_seconds, now=now_dt)
    token = secrets.token_urlsafe(16)

    async def _try_claim(candidate: WorkItem) -> Optional[WorkItem]:
        if _deadline_passed(candidate, now_dt):
            try:
                await expire_work_item(candidate.work_item_id)
            except WorkError:
                pass
            return None
        if not _is_due(candidate, now_dt):
            return None
        if not await _dependencies_allow_claim(candidate):
            return None
        if candidate.cancel_requested_at:
            try:
                await cancel_work_item(candidate.work_item_id)
            except WorkError:
                pass
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
        candidates = list(await WorkItem.find({"context.status": status}))
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
    item = await WorkItem.get(_object_id(work_item_id))
    if item is not None and item.cancel_requested_at and target == "succeeded":
        raise WorkError("work.cancelled", "cancel requested; completion blocked")
    if item is not None and _deadline_passed(item, datetime.now(timezone.utc)):
        if target not in {"expired", "cancelled", "failed", "dead_letter"}:
            raise WorkError("work.deadline_exceeded", "deadline passed")
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
    "cancel_work_item",
    "claim_due_candidate",
    "compute_retry_delay",
    "continuation_fingerprint",
    "enqueue_work_item",
    "expire_work_item",
    "force_expire_lease_for_tests",
    "heartbeat_lease",
    "input_fingerprint",
    "normalize_failure",
    "reclaim_expired_lease",
    "recommended_heartbeat_interval",
    "schedule_retry",
    "transition_leased",
    "transition_work_item",
    "work_item_object_id",
]
