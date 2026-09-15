"""Phase 7 Plan 07-04 — approval review REST surface (UX-03).

Three endpoints:

    GET  /api/approvals?policy_id=&actor_kind=&status=&scope=
            -> ApprovalListResponse
    POST /api/approvals/{id}/approve
            -> ApprovalDecisionResponse (status='approved') / HTTP 202 if
               dispatch failure or 422 if action unsupported
    POST /api/approvals/{id}/reject  (body: ApprovalRejectRequest)
            -> ApprovalDecisionResponse (status='rejected')

Approver permission: ``approval.approve`` PolicyAction on a workspace
admin scope (resolved via the resource's underlying workspace where
applicable). v1 uses scope=f"policy:{ap.policy_id}" since the resource's
workspace is not always derivable from the approval row (e.g. entry.create
on a not-yet-created entry has no workspace ID). Future hardening: derive
workspace from resource_id when available.

Per-record permission filter: each row gated via
``policy_evaluate(action='approval.approve', resource=Resource(
kind='policy', id=ap.policy_id, scope=f'policy:{ap.policy_id}'))``;
denied rows dropped from the response so the caller only sees what they
can act on.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import Request
from jvspatial.api import endpoint

from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    MissingAuthenticationError,
    ResourceNotFoundError,
)
from app.api.utils import resolve_principal_id
from app.models.nodes import Approval
from app.schemas.approvals import (
    ApprovalDecisionResponse,
    ApprovalListResponse,
    ApprovalResponse,
)
from app.schemas.policy import Resource, Subject
from app.services.approval_executor import (
    ApprovalAlreadyDecided,
    UnsupportedApprovalAction,
    execute_approval,
    reject_approval,
)
from app.services.policy_engine import evaluate as policy_evaluate

logger = logging.getLogger(__name__)


def _approval_to_response(ap: Approval) -> Dict[str, Any]:
    """Materialize an Approval Node to wire JSON.

    The Pydantic boundary (ApprovalResponse) enforces ActorKind +
    PolicyAction Literals; the persisted Node stores both as ``str``
    (same idiom as ChangeEvent.actor_kind / Actor.kind).
    """
    return ApprovalResponse(
        id=ap.id,
        actor_kind=ap.actor_kind,  # type: ignore[arg-type]
        actor_id=ap.actor_id,
        action=ap.action,  # type: ignore[arg-type]
        resource_kind=ap.resource_kind,
        resource_id=ap.resource_id,
        payload=dict(ap.payload or {}),
        policy_id=ap.policy_id,
        created_at=ap.created_at,
        expires_at=ap.expires_at,
        status=ap.status,
        decided_at=ap.decided_at,
        decider_id=ap.decider_id,
    ).model_dump()


@endpoint("/approvals", methods=["GET"], auth=True, tags=["Approvals"])
async def list_approvals(
    request: Request,
    policy_id: Optional[str] = None,
    actor_kind: Optional[str] = None,
    status: Optional[str] = None,
    scope: Optional[str] = None,
) -> Dict[str, Any]:
    """List Approval rows visible to the caller.

    Per-record permission filter: each row gated via
    ``policy_evaluate(action='approval.approve', ...)``; denied rows
    dropped from the response.

    Query params:
      - ``policy_id`` — filter by source Policy
      - ``actor_kind`` — filter by ActorKind (typically 'agent')
      - ``status`` — filter by status (default: 'pending')
      - ``scope`` — filter by scope-string prefix (e.g. 'track:t-1')
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    storage_filter: Dict[str, Any] = {}
    if status:
        storage_filter["status"] = status
    else:
        storage_filter["status"] = "pending"

    rows = await Approval.find(storage_filter)

    visible: List[Dict[str, Any]] = []
    for ap in rows:
        if policy_id and ap.policy_id != policy_id:
            continue
        if actor_kind and ap.actor_kind != actor_kind:
            continue
        if scope:
            row_scope = f"{ap.resource_kind}:{ap.resource_id}"
            if not row_scope.startswith(scope):
                continue

        decision = await policy_evaluate(
            subject=Subject(kind="human", id=user_id),
            action="approval.approve",
            resource=Resource(
                kind="policy",
                id=ap.policy_id,
                scope=f"policy:{ap.policy_id}",
            ),
        )
        if not decision.allowed:
            continue
        visible.append(_approval_to_response(ap))

    return ApprovalListResponse(
        approvals=visible,  # type: ignore[arg-type]
        next_cursor=None,
        has_more=False,
    ).model_dump()


@endpoint(
    "/approvals/{approval_id}/approve",
    methods=["POST"],
    auth=True,
    tags=["Approvals"],
)
async def approve_approval(request: Request, approval_id: str) -> Dict[str, Any]:
    """Approve and re-run a deferred agent write.

    Gates on ``approval.approve`` PolicyAction (workspace admin). On allow:
    marks ap.status='approved' BEFORE re-run (idempotency), dispatches via
    WRITE_ACTION_DISPATCH; on dispatch failure reverts ap.status='pending'
    and re-raises (never silently absorb).

    Errors:
      403 InsufficientPermissionsError — caller lacks approve permission
      404 ResourceNotFoundError       — approval_id not found
      409 Conflict                     — ap.status != 'pending'
      410 Gone                         — ap.status == 'expired'
      422 BadRequest                   — action not in WRITE_ACTION_DISPATCH
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    ap = await Approval.get(approval_id)
    if ap is None:
        raise ResourceNotFoundError(message="Approval not found")
    if ap.status == "expired":
        raise BadRequestError(
            message="Approval has expired and cannot be approved",
            details={"error_code": "approval.expired"},
        )
    if ap.status != "pending":
        raise BadRequestError(
            message=f"Approval already decided (status={ap.status})",
            details={
                "error_code": "approval.already_decided",
                "status": ap.status,
            },
        )

    decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="approval.approve",
        resource=Resource(
            kind="policy",
            id=ap.policy_id,
            scope=f"policy:{ap.policy_id}",
        ),
    )
    if not decision.allowed:
        raise InsufficientPermissionsError(
            message="Caller lacks approval.approve permission"
        )

    try:
        result = await execute_approval(
            approval_id=approval_id,
            approver_user_id=user_id,
        )
    except UnsupportedApprovalAction as exc:
        raise BadRequestError(
            message=str(exc),
            details={"error_code": "approval.unsupported_action"},
        )
    except ApprovalAlreadyDecided as exc:
        raise BadRequestError(
            message=str(exc),
            details={"error_code": "approval.already_decided"},
        )

    return ApprovalDecisionResponse(
        approval_id=approval_id,
        status="approved",
        result=result,
    ).model_dump()


@endpoint(
    "/approvals/{approval_id}/reject",
    methods=["POST"],
    auth=True,
    tags=["Approvals"],
)
async def reject_approval_endpoint(
    request: Request, approval_id: str
) -> Dict[str, Any]:
    """Reject a deferred agent write.

    Body: ``{"reason": str | null}`` — optional rejection reason surfaced
    on the policy.deny ChangeEvent details payload.

    Gates on ``approval.reject`` PolicyAction. On allow: marks
    ap.status='rejected'; emits policy.deny ChangeEvent (I-APPROVAL-02 —
    NEVER the original action).
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    reason = (body or {}).get("reason") if isinstance(body, dict) else None

    ap = await Approval.get(approval_id)
    if ap is None:
        raise ResourceNotFoundError(message="Approval not found")
    if ap.status != "pending":
        raise BadRequestError(
            message=f"Approval already decided (status={ap.status})",
            details={
                "error_code": "approval.already_decided",
                "status": ap.status,
            },
        )

    decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="approval.reject",
        resource=Resource(
            kind="policy",
            id=ap.policy_id,
            scope=f"policy:{ap.policy_id}",
        ),
    )
    if not decision.allowed:
        raise InsufficientPermissionsError(
            message="Caller lacks approval.reject permission"
        )

    result = await reject_approval(
        approval_id=approval_id,
        rejecter_user_id=user_id,
        reason=reason if isinstance(reason, str) else None,
    )

    return ApprovalDecisionResponse(
        approval_id=approval_id,
        status=result["status"],
        result=None,
    ).model_dump()
