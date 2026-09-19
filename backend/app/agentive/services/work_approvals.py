"""Fail-closed durable WorkApproval authority (Task 7)."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Literal, Optional, Tuple

from jvspatial.db import get_prime_database

from app.agentive.services.work_outbox import (
    OBJECT_COLLECTION,
    TOPIC_TRANSITIONED,
    _dev_lock,
    _is_postgres_txn_db,
    _txn_database,
    build_outbox_document,
    outbox_id_for,
)
from app.agentive.work_models import WorkApproval, WorkItem
from app.schemas.agentive.work import WorkError
from app.utils.time import utc_now_iso

log = logging.getLogger(__name__)

Decision = Literal["approved", "rejected", "expired"]


def work_approval_object_id(work_approval_id: str) -> str:
    if work_approval_id.startswith("o.WorkApproval."):
        return work_approval_id
    return f"o.WorkApproval.{work_approval_id}"


def deterministic_work_approval_id(
    *, work_item_id: str, staging_token: str = "", policy_approval_id: str = ""
) -> str:
    """Stable id for one human-wait slot on a WorkItem."""
    key = staging_token or policy_approval_id or "none"
    digest = hashlib.sha256(f"{work_item_id}:{key}".encode("utf-8")).hexdigest()
    return digest


def build_work_approval_document(
    *,
    work_approval_id: str,
    work_item_id: str,
    staging_token: str,
    policy_approval_id: str,
    run_id: str,
    run_step_id: str,
    authority_digest: str,
    expires_at: str,
    created_at: str,
    status: str = "pending",
) -> Dict[str, Any]:
    return {
        "id": work_approval_object_id(work_approval_id),
        "entity": "WorkApproval",
        "context": {
            "work_approval_id": work_approval_id,
            "work_item_id": work_item_id,
            "status": status,
            "staging_token": staging_token,
            "policy_approval_id": policy_approval_id,
            "run_id": run_id,
            "run_step_id": run_step_id,
            "authority_digest": authority_digest,
            "decision": "",
            "decider_id": "",
            "expires_at": expires_at,
            "decided_at": "",
            "created_at": created_at,
            "updated_at": created_at,
        },
    }


def _hydrate_approval(doc: Dict[str, Any]) -> WorkApproval:
    ctx = dict(doc.get("context") or {})
    return WorkApproval(id=doc["id"], **ctx)


async def get_work_approval(work_approval_id: str) -> Optional[WorkApproval]:
    return await WorkApproval.get(work_approval_object_id(work_approval_id))


async def get_pending_by_staging_token(staging_token: str) -> Optional[WorkApproval]:
    token = (staging_token or "").strip()
    if not token:
        return None
    found = await WorkApproval.find(
        {"context.staging_token": token, "context.status": "pending"}
    )
    return found[0] if found else None


async def get_pending_by_work_item(work_item_id: str) -> Optional[WorkApproval]:
    wid = (work_item_id or "").strip()
    if not wid:
        return None
    found = await WorkApproval.find(
        {"context.work_item_id": wid, "context.status": "pending"}
    )
    return found[0] if found else None


async def get_by_policy_approval_id(policy_approval_id: str) -> Optional[WorkApproval]:
    pid = (policy_approval_id or "").strip()
    if not pid:
        return None
    found = await WorkApproval.find({"context.policy_approval_id": pid})
    return found[0] if found else None


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


async def _persist_staged_projection(fields: Dict[str, Any]) -> None:
    """Best-effort StagedChangeRecord upsert (dev path / after txn)."""
    if not fields:
        return
    try:
        from app.agentive.staging_store import StagedChangeRecord, _find_record

        token = str(fields.get("token") or "").strip()
        if not token:
            return
        # Ensure work_approval_id rides on the projection.
        existing = await _find_record(token)
        if existing is not None:
            for key, val in fields.items():
                if hasattr(existing, key):
                    setattr(existing, key, val)
            # Stash authority link in payload when model lacks the field.
            payload = dict(getattr(existing, "payload", None) or {})
            if fields.get("work_approval_id"):
                payload["work_approval_id"] = fields["work_approval_id"]
            existing.payload = payload
            if "state" not in fields or existing.state not in ("pending", "blessed"):
                existing.state = existing.state or "pending"
            await existing.save()
            return
        create_fields = {
            k: v
            for k, v in fields.items()
            if k
            in {
                "token",
                "user_id",
                "session_id",
                "kind",
                "summary",
                "diff_human",
                "diff_machine",
                "payload",
                "created_at",
                "expires_at",
                "state",
                "autonomy_grant_used",
                "interaction_id",
                "workspace_id",
                "last_error",
                "progress",
                "blessed_at",
                "resolved_at",
                "idempotency_key",
            }
        }
        payload = dict(create_fields.get("payload") or {})
        if fields.get("work_approval_id"):
            payload["work_approval_id"] = fields["work_approval_id"]
        create_fields["payload"] = payload
        create_fields.setdefault("state", "pending")
        create_fields.setdefault("token", token)
        if not create_fields.get("created_at"):
            create_fields["created_at"] = utc_now_iso()
        if not create_fields.get("expires_at"):
            create_fields["expires_at"] = utc_now_iso()
        await StagedChangeRecord.create(**create_fields)
    except Exception:  # noqa: BLE001
        log.warning("work_approvals staged projection failed", exc_info=True)


async def propose_work_approval_unit(
    *,
    work_item_id: str,
    lease_token: str,
    lease_fence: int,
    run_id: str,
    run_step_id: str,
    staging_token: str = "",
    staged_change_fields: Optional[Dict[str, Any]] = None,
    authority_digest: str,
    expires_at: str = "",
    policy_approval_id: str = "",
    transaction: Any = None,
) -> Tuple[WorkApproval, WorkItem]:
    """Atomic unit 3: WorkApproval + projection + running→waiting_for_human.

    Fail-closed: missing staging_token AND policy_approval_id raises.
    """
    if not (staging_token or "").strip() and not (policy_approval_id or "").strip():
        raise WorkError(
            "work.approval_required",
            "propose requires staging_token or policy_approval_id",
        )
    if not (authority_digest or "").strip():
        raise WorkError("work.approval_required", "authority_digest required")

    wa_id = deterministic_work_approval_id(
        work_item_id=work_item_id,
        staging_token=staging_token,
        policy_approval_id=policy_approval_id,
    )
    now = utc_now_iso()
    existing = await get_work_approval(wa_id)
    if existing is not None:
        item = await WorkItem.get(f"o.WorkItem.{work_item_id}")
        if item is None:
            raise WorkError("work.not_found", f"work item {work_item_id} not found")
        if existing.status == "pending" and item.status == "waiting_for_human":
            return existing, item
        if existing.status != "pending":
            raise WorkError(
                "work.approval_decided",
                f"work approval already {existing.status}",
            )

    doc = build_work_approval_document(
        work_approval_id=wa_id,
        work_item_id=work_item_id,
        staging_token=staging_token or "",
        policy_approval_id=policy_approval_id or "",
        run_id=run_id or "",
        run_step_id=run_step_id or "",
        authority_digest=authority_digest,
        expires_at=expires_at or "",
        created_at=now,
    )

    db = get_prime_database()
    if transaction is not None or _is_postgres_txn_db(db):
        approval, item = await _propose_postgres(
            db=_txn_database(db) if transaction is None else db,
            transaction=transaction,
            doc=doc,
            work_item_id=work_item_id,
            lease_token=lease_token,
            lease_fence=lease_fence,
            now=now,
        )
    else:
        async with _dev_lock:
            approval, item = await _propose_dev(
                doc=doc,
                work_item_id=work_item_id,
                lease_token=lease_token,
                lease_fence=lease_fence,
                now=now,
            )

    projection = dict(staged_change_fields or {})
    if staging_token:
        projection.setdefault("token", staging_token)
    projection["work_approval_id"] = wa_id
    await _persist_staged_projection(projection)
    return approval, item


async def _propose_dev(
    *,
    doc: Dict[str, Any],
    work_item_id: str,
    lease_token: str,
    lease_fence: int,
    now: str,
) -> Tuple[WorkApproval, WorkItem]:
    from app.agentive.services.work_outbox import _cas_dev

    ctx = dict(doc["context"])
    approval, created = await WorkApproval.create_if_absent(id=doc["id"], **ctx)
    if not created and approval.status != "pending":
        raise WorkError(
            "work.approval_decided",
            f"work approval already {approval.status}",
        )
    # Call unlocked CAS body — caller already holds `_dev_lock`.
    item = await _cas_dev(
        object_id=f"o.WorkItem.{work_item_id}",
        bare_id=work_item_id,
        expected={
            "status": "running",
            "lease_token": lease_token,
            "lease_fence": int(lease_fence),
        },
        updates={
            "status": "waiting_for_human",
            "lease_token": "",
            "lease_owner": "",
            "lease_expires_at": "",
            "run_id": ctx.get("run_id") or "",
        },
        outbox_topic=TOPIC_TRANSITIONED,
        outbox_payload={
            "from_status": "running",
            "to_status": "waiting_for_human",
            "work_approval_id": ctx["work_approval_id"],
        },
        bump_transition_seq=True,
        error_code="work.lease_lost",
        now=now,
    )
    return approval, item


async def _propose_postgres(
    *,
    db: Any,
    transaction: Any,
    doc: Dict[str, Any],
    work_item_id: str,
    lease_token: str,
    lease_fence: int,
    now: str,
) -> Tuple[WorkApproval, WorkItem]:
    owns_txn = transaction is None
    txn = transaction
    if owns_txn:
        txn = await db.begin_transaction()
    object_id = (
        work_item_id
        if work_item_id.startswith("o.WorkItem.")
        else f"o.WorkItem.{work_item_id}"
    )
    bare_id = object_id.removeprefix("o.WorkItem.")
    try:
        inserted = await txn.insert_if_absent(OBJECT_COLLECTION, doc)
        if not inserted:
            existing_doc = await txn.get(OBJECT_COLLECTION, doc["id"])
            if existing_doc is None:
                raise WorkError("work.cas_conflict", "approval insert raced")
            existing_ctx = dict(existing_doc.get("context") or {})
            if existing_ctx.get("status") != "pending":
                raise WorkError(
                    "work.approval_decided",
                    f"work approval already {existing_ctx.get('status')}",
                )
        current = await txn.get(OBJECT_COLLECTION, object_id)
        if current is None:
            raise WorkError("work.not_found", f"work item {bare_id} not found")
        ctx = dict(current.get("context") or {})
        if (
            ctx.get("status") != "running"
            or ctx.get("lease_token") != lease_token
            or int(ctx.get("lease_fence") or 0) != int(lease_fence)
        ):
            raise WorkError(
                "work.lease_lost", "lease mismatch while proposing approval"
            )
        next_seq = int(ctx.get("transition_seq") or 0) + 1
        updated = await txn.find_one_and_update(
            OBJECT_COLLECTION,
            {
                "id": object_id,
                "context.status": "running",
                "context.lease_token": lease_token,
                "context.lease_fence": int(lease_fence),
            },
            {
                "$set": {
                    "context.status": "waiting_for_human",
                    "context.lease_token": "",
                    "context.lease_owner": "",
                    "context.lease_expires_at": "",
                    "context.transition_seq": next_seq,
                    "context.updated_at": now,
                    "context.run_id": doc["context"].get("run_id") or "",
                }
            },
        )
        if updated is None:
            raise WorkError("work.lease_lost", "concurrent lease or state change")
        oid = outbox_id_for(
            work_item_id=bare_id, topic=TOPIC_TRANSITIONED, seq=next_seq
        )
        await txn.insert_if_absent(
            OBJECT_COLLECTION,
            build_outbox_document(
                outbox_id=oid,
                work_item_id=bare_id,
                topic=TOPIC_TRANSITIONED,
                payload={
                    "from_status": "running",
                    "to_status": "waiting_for_human",
                    "work_approval_id": doc["context"]["work_approval_id"],
                },
                created_at=now,
            ),
        )
        if owns_txn:
            await db.commit_transaction(txn)
        from app.agentive.services.work_outbox import (
            _hydrate_work_item,
            _refresh_work_item_cache,
        )

        approval = _hydrate_approval(doc)
        item = _hydrate_work_item(updated)
        await _refresh_work_item_cache(item)
        return approval, item
    except Exception:
        if owns_txn and txn is not None:
            await db.rollback_transaction(txn)
        raise


async def decide_work_approval_unit(
    *,
    work_approval_id: str,
    decision: Decision,
    decider_id: str = "",
    reason: str = "",
    transaction: Any = None,
) -> Tuple[WorkApproval, WorkItem]:
    """Atomic unit 4: one-decision CAS + WorkItem transition + outbox."""
    if decision not in {"approved", "rejected", "expired"}:
        raise WorkError("work.invalid_transition", f"bad decision {decision!r}")

    approval = await get_work_approval(work_approval_id)
    if approval is None:
        raise WorkError("work.not_found", f"work approval {work_approval_id} not found")
    if approval.status != "pending":
        # Idempotent replay of the same decision.
        item = await WorkItem.get(f"o.WorkItem.{approval.work_item_id}")
        if item is None:
            raise WorkError(
                "work.not_found", f"work item {approval.work_item_id} not found"
            )
        if approval.status == decision:
            return approval, item
        raise WorkError(
            "work.approval_decided",
            f"work approval already {approval.status}",
        )

    now = utc_now_iso()
    if decision == "approved":
        target_status = "queued"
        failure = None
        next_attempt_at = now
    elif decision == "rejected":
        target_status = "failed"
        from app.agentive.services.work_items import normalize_failure

        failure = normalize_failure(
            class_="policy_denied",
            code="work.approval_rejected",
            message=reason or "approval rejected",
            retryable=False,
        )
        next_attempt_at = ""
    else:
        target_status = "expired"
        from app.agentive.services.work_items import normalize_failure

        failure = normalize_failure(
            class_="deadline_exceeded",
            code="work.approval_expired",
            message=reason or "approval expired",
            retryable=False,
        )
        next_attempt_at = ""

    db = get_prime_database()
    if transaction is not None or _is_postgres_txn_db(db):
        return await _decide_postgres(
            db=_txn_database(db) if transaction is None else db,
            transaction=transaction,
            approval=approval,
            decision=decision,
            decider_id=decider_id,
            target_status=target_status,
            failure=failure,
            next_attempt_at=next_attempt_at,
            now=now,
        )
    async with _dev_lock:
        return await _decide_dev(
            approval=approval,
            decision=decision,
            decider_id=decider_id,
            target_status=target_status,
            failure=failure,
            next_attempt_at=next_attempt_at,
            now=now,
        )


async def _decide_dev(
    *,
    approval: WorkApproval,
    decision: Decision,
    decider_id: str,
    target_status: str,
    failure: Optional[Dict[str, Any]],
    next_attempt_at: str,
    now: str,
) -> Tuple[WorkApproval, WorkItem]:
    from app.agentive.services.work_outbox import _cas_dev

    # One-decision CAS on the approval row.
    fresh = await WorkApproval.get(approval.id)
    if fresh is None or fresh.status != "pending":
        raise WorkError("work.approval_decided", "approval no longer pending")
    fresh.status = decision
    fresh.decision = decision
    fresh.decider_id = decider_id or ""
    fresh.decided_at = now
    fresh.updated_at = now
    await fresh.save()

    updates: Dict[str, Any] = {
        "status": target_status,
        "lease_token": "",
        "lease_owner": "",
        "lease_expires_at": "",
    }
    if next_attempt_at:
        updates["next_attempt_at"] = next_attempt_at
    if failure is not None:
        updates["failure"] = failure

    # Caller already holds `_dev_lock`.
    item = await _cas_dev(
        object_id=f"o.WorkItem.{approval.work_item_id}",
        bare_id=approval.work_item_id,
        expected={"status": "waiting_for_human"},
        updates=updates,
        outbox_topic=TOPIC_TRANSITIONED,
        outbox_payload={
            "from_status": "waiting_for_human",
            "to_status": target_status,
            "work_approval_id": approval.work_approval_id,
            "decision": decision,
        },
        bump_transition_seq=True,
        error_code="work.cas_conflict",
        now=now,
    )
    return fresh, item


async def _decide_postgres(
    *,
    db: Any,
    transaction: Any,
    approval: WorkApproval,
    decision: Decision,
    decider_id: str,
    target_status: str,
    failure: Optional[Dict[str, Any]],
    next_attempt_at: str,
    now: str,
) -> Tuple[WorkApproval, WorkItem]:
    owns_txn = transaction is None
    txn = transaction
    if owns_txn:
        txn = await db.begin_transaction()
    wa_oid = work_approval_object_id(approval.work_approval_id)
    wi_oid = f"o.WorkItem.{approval.work_item_id}"
    try:
        decided = await txn.find_one_and_update(
            OBJECT_COLLECTION,
            {"id": wa_oid, "context.status": "pending"},
            {
                "$set": {
                    "context.status": decision,
                    "context.decision": decision,
                    "context.decider_id": decider_id or "",
                    "context.decided_at": now,
                    "context.updated_at": now,
                }
            },
        )
        if decided is None:
            raise WorkError("work.approval_decided", "approval no longer pending")

        set_fields: Dict[str, Any] = {
            "context.status": target_status,
            "context.lease_token": "",
            "context.lease_owner": "",
            "context.lease_expires_at": "",
            "context.updated_at": now,
        }
        # Read current seq for outbox.
        current = await txn.get(OBJECT_COLLECTION, wi_oid)
        if current is None:
            raise WorkError(
                "work.not_found", f"work item {approval.work_item_id} not found"
            )
        ctx = dict(current.get("context") or {})
        if ctx.get("status") != "waiting_for_human":
            raise WorkError(
                "work.invalid_transition",
                f"expected waiting_for_human, found {ctx.get('status')}",
            )
        next_seq = int(ctx.get("transition_seq") or 0) + 1
        set_fields["context.transition_seq"] = next_seq
        if next_attempt_at:
            set_fields["context.next_attempt_at"] = next_attempt_at
        if failure is not None:
            set_fields["context.failure"] = failure

        updated = await txn.find_one_and_update(
            OBJECT_COLLECTION,
            {"id": wi_oid, "context.status": "waiting_for_human"},
            {"$set": set_fields},
        )
        if updated is None:
            raise WorkError("work.cas_conflict", "work item transition raced")
        oid = outbox_id_for(
            work_item_id=approval.work_item_id,
            topic=TOPIC_TRANSITIONED,
            seq=next_seq,
        )
        await txn.insert_if_absent(
            OBJECT_COLLECTION,
            build_outbox_document(
                outbox_id=oid,
                work_item_id=approval.work_item_id,
                topic=TOPIC_TRANSITIONED,
                payload={
                    "from_status": "waiting_for_human",
                    "to_status": target_status,
                    "work_approval_id": approval.work_approval_id,
                    "decision": decision,
                },
                created_at=now,
            ),
        )
        if owns_txn:
            await db.commit_transaction(txn)
        from app.agentive.services.work_outbox import (
            _hydrate_work_item,
            _refresh_work_item_cache,
        )

        item = _hydrate_work_item(updated)
        await _refresh_work_item_cache(item)
        return _hydrate_approval(decided), item
    except Exception:
        if owns_txn and txn is not None:
            await db.rollback_transaction(txn)
        raise


async def approve_work_approval(
    *,
    work_approval_id: str,
    decider_id: str,
    transaction: Any = None,
) -> Tuple[WorkApproval, WorkItem]:
    return await decide_work_approval_unit(
        work_approval_id=work_approval_id,
        decision="approved",
        decider_id=decider_id,
        transaction=transaction,
    )


async def reject_work_approval(
    *,
    work_approval_id: str,
    decider_id: str,
    reason: str = "",
    transaction: Any = None,
) -> Tuple[WorkApproval, WorkItem]:
    return await decide_work_approval_unit(
        work_approval_id=work_approval_id,
        decision="rejected",
        decider_id=decider_id,
        reason=reason,
        transaction=transaction,
    )


async def expire_work_approval(
    *,
    work_approval_id: str,
    transaction: Any = None,
) -> Tuple[WorkApproval, WorkItem]:
    return await decide_work_approval_unit(
        work_approval_id=work_approval_id,
        decision="expired",
        transaction=transaction,
    )


async def expire_overdue_work_approvals(*, now: Optional[str] = None) -> int:
    """Expire pending approvals past expires_at. Returns count expired."""
    cutoff = _parse_iso(now) or datetime.now(timezone.utc)
    pending = await WorkApproval.find({"context.status": "pending"})
    count = 0
    for approval in pending or []:
        exp = _parse_iso(getattr(approval, "expires_at", "") or "")
        if exp is None:
            continue
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp > cutoff:
            continue
        try:
            await expire_work_approval(work_approval_id=approval.work_approval_id)
            count += 1
        except WorkError:
            continue
    return count


__all__ = [
    "approve_work_approval",
    "build_work_approval_document",
    "decide_work_approval_unit",
    "deterministic_work_approval_id",
    "expire_overdue_work_approvals",
    "expire_work_approval",
    "get_by_policy_approval_id",
    "get_pending_by_staging_token",
    "get_pending_by_work_item",
    "get_work_approval",
    "propose_work_approval_unit",
    "reject_work_approval",
    "work_approval_object_id",
]
