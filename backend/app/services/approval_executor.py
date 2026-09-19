"""Phase 7 Plan 07-04 — approval executor.

``execute_approval(approval_id, approver_user_id)`` resolves the Approval,
runs the ``approval.approve`` policy gate, marks the row
``status='approved'`` (idempotency — re-run failure must not leave
status='pending'), dispatches the original write via
``WRITE_ACTION_DISPATCH`` to the matching pure-service helper, threads
``_internal_actor=Subject(kind='system', id='approval_approve')`` through
the helper (I-APPROVAL-01 — recursion guard), and on dispatch failure
REVERTS ``status='pending'`` before re-raising (never silently absorb).

v1 supports auto-approve re-run for EXACTLY 6 actions:

    entry.create   -> create_entry_internal
    entry.update   -> update_entry_internal
    entry.delete   -> delete_entry_internal
    track.create   -> create_track_internal
    app.create   -> create_app_internal
    comment.create -> create_comment_internal

Other actions persist an Approval (the intercept fires) but
``execute_approval`` returns a structured 422 for unsupported actions —
the human must manually re-run via the original tool.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict, Optional

from app.models.nodes import Approval
from app.schemas.policy import Subject
from app.services.app_writer import create_app_internal
from app.services.comment_writer import create_comment_internal
from app.services.entry_writer import (
    create_entry_internal,
    delete_entry_internal,
    update_entry_internal,
)
from app.services.track_writer import create_track_internal
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)


# Exactly 6 entries. Adding a new entry requires:
#   1. New PolicyAction member (or existing one — most likely existing).
#   2. New pure-service helper in app/services/*_writer.py.
#   3. New test in tests/test_approval_executor.py.
WRITE_ACTION_DISPATCH: Dict[str, Callable[..., Awaitable[Dict[str, Any]]]] = {
    "entry.create": create_entry_internal,
    "entry.update": update_entry_internal,
    "entry.delete": delete_entry_internal,
    "track.create": create_track_internal,
    "app.create": create_app_internal,
    "comment.create": create_comment_internal,
}


class UnsupportedApprovalAction(Exception):
    """Raised when execute_approval is asked to re-run an action not in
    WRITE_ACTION_DISPATCH. v1 limitation — the human must manually re-run
    the original tool. The REST handler catches this and surfaces it as
    422 with error_code='approval.unsupported_action'.
    """


class ApprovalAlreadyDecided(Exception):
    """Raised when execute_approval or reject_approval is called on a row
    whose status is no longer 'pending' (approved, rejected, expired).
    Idempotency guard — the REST handler surfaces this as 409.
    """


async def execute_approval(
    *,
    approval_id: str,
    approver_user_id: str,
) -> Dict[str, Any]:
    """Run the deferred write through the matching pure-service helper.

    Pre-condition: the caller already ran the ``approval.approve``
    PolicyAction gate via ``policy_engine.evaluate`` — this function does
    NOT re-gate that. It writes idempotency state, dispatches, and
    reverts on failure.

    Returns the exported node from the re-run (e.g. created Entry).
    """
    ap = await Approval.get(approval_id)
    if ap is None:
        raise ValueError(f"execute_approval: approval {approval_id!r} not found")
    if ap.status != "pending":
        raise ApprovalAlreadyDecided(
            f"execute_approval: ap.status={ap.status!r} (expected 'pending')"
        )

    helper = WRITE_ACTION_DISPATCH.get(ap.action)
    if helper is None:
        raise UnsupportedApprovalAction(
            f"execute_approval: action {ap.action!r} not in WRITE_ACTION_DISPATCH "
            f"(v1 supports: {sorted(WRITE_ACTION_DISPATCH.keys())})"
        )

    # Idempotency — mark approved BEFORE dispatch so a concurrent approve
    # call sees status != 'pending' and short-circuits.
    ap.status = "approved"
    decided_at = utc_now_iso()
    ap.decided_at = decided_at
    ap.decider_id = approver_user_id
    await ap.save()
    # Linked durable WorkApproval (scalar policy_approval_id) requeues work.
    try:
        from app.agentive.services import work_approvals
        from app.schemas.agentive.work import WorkError

        linked = await work_approvals.get_by_policy_approval_id(approval_id)
        if linked is not None and linked.status == "pending":
            await work_approvals.approve_work_approval(
                work_approval_id=linked.work_approval_id,
                decider_id=approver_user_id,
            )
    except WorkError as exc:
        if getattr(exc, "code", "") != "work.approval_decided":
            logger.warning(
                "execute_approval: work approval decide failed for %s: %s",
                approval_id,
                exc,
            )
    except Exception:  # noqa: BLE001
        logger.warning(
            "execute_approval: work approval link failed for %s",
            approval_id,
            exc_info=True,
        )
    # Phase 10.5 Plan 10.5-05 (I-GRAPH-01): wire decider User
    # -HAS_APPROVAL_DECISION-> Approval. Best-effort, fail-loud via
    # log; revert path below clears the edge implicitly via cascade
    # delete if needed (status reverts to pending but edge remains —
    # idempotent on retry).
    try:
        from app.models.edges import HAS_APPROVAL_DECISION
        from app.services.permissions import get_user_node

        decider = await get_user_node(approver_user_id)
        if decider is not None:
            await decider.connect(
                ap,
                edge=HAS_APPROVAL_DECISION,
                decided_at=decided_at,
                decision="approved",
            )
    except Exception:
        logger.warning(
            "execute_approval: HAS_APPROVAL_DECISION wire failed for "
            "approval=%s decider=%s",
            approval_id,
            approver_user_id,
        )

    # Dispatch — thread _internal_actor for the recursion guard
    # (I-APPROVAL-01). On failure: revert ap.status BEFORE re-raising so
    # the row can be retried.
    try:
        result = await helper(
            actor_kind=ap.actor_kind,  # original agent — I-APPROVAL-02
            actor_id=ap.actor_id,
            payload=dict(ap.payload),
            _internal_actor=Subject(kind="system", id="approval_approve"),
        )
    except Exception as exc:
        logger.warning(
            "execute_approval: dispatch failed for approval=%s action=%s — "
            "reverting status to pending: %s",
            approval_id,
            ap.action,
            exc,
        )
        ap.status = "pending"
        ap.decided_at = None
        ap.decider_id = None
        try:
            await ap.save()
        except Exception as save_exc:  # noqa: BLE001
            logger.error(
                "execute_approval: revert save FAILED for approval=%s: %s",
                approval_id,
                save_exc,
            )
        raise

    return result


async def reject_approval(
    *,
    approval_id: str,
    rejecter_user_id: str,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Mark an Approval as rejected and emit ``policy.deny`` ChangeEvent.

    I-APPROVAL-02 — reject NEVER emits the original action; the audit
    signal is exclusively ``policy.deny`` with details={approval_id,
    original_action, agent_id, reason}.

    Pre-condition: caller already ran the ``approval.reject`` PolicyAction
    gate.
    """
    from app.services.change_event import emit_change_event

    ap = await Approval.get(approval_id)
    if ap is None:
        raise ValueError(f"reject_approval: approval {approval_id!r} not found")
    if ap.status != "pending":
        raise ApprovalAlreadyDecided(
            f"reject_approval: ap.status={ap.status!r} (expected 'pending')"
        )

    ap.status = "rejected"
    decided_at = utc_now_iso()
    ap.decided_at = decided_at
    ap.decider_id = rejecter_user_id
    await ap.save()
    try:
        from app.agentive.services import work_approvals
        from app.schemas.agentive.work import WorkError

        linked = await work_approvals.get_by_policy_approval_id(approval_id)
        if linked is not None and linked.status == "pending":
            await work_approvals.reject_work_approval(
                work_approval_id=linked.work_approval_id,
                decider_id=rejecter_user_id,
                reason=reason or "",
            )
    except WorkError as exc:
        if getattr(exc, "code", "") != "work.approval_decided":
            logger.warning(
                "reject_approval: work approval decide failed for %s: %s",
                approval_id,
                exc,
            )
    except Exception:  # noqa: BLE001
        logger.warning(
            "reject_approval: work approval link failed for %s",
            approval_id,
            exc_info=True,
        )
    # Phase 10.5 Plan 10.5-05 (I-GRAPH-01): wire decider User
    # -HAS_APPROVAL_DECISION-> Approval. Best-effort.
    try:
        from app.models.edges import HAS_APPROVAL_DECISION
        from app.services.permissions import get_user_node

        decider = await get_user_node(rejecter_user_id)
        if decider is not None:
            await decider.connect(
                ap,
                edge=HAS_APPROVAL_DECISION,
                decided_at=decided_at,
                decision="rejected",
            )
    except Exception:
        logger.warning(
            "reject_approval: HAS_APPROVAL_DECISION wire failed for "
            "approval=%s decider=%s",
            approval_id,
            rejecter_user_id,
        )

    await emit_change_event(
        actor_kind="human",
        actor_id=rejecter_user_id,
        action="policy.deny",
        resource_type=ap.resource_kind or "approval",
        resource_id=ap.resource_id or approval_id,
        before=None,
        after=None,
        scope=f"approval:{approval_id}",
        details={
            "approval_id": approval_id,
            "original_action": ap.action,
            "agent_id": ap.actor_id,
            "reason": reason or "",
        },
    )

    return {
        "approval_id": approval_id,
        "status": "rejected",
    }
