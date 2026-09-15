"""Approval Pydantic schemas — wire shape for the approval review surface.

Phase 7 Plan 07-04 (UX-03). The ``Approval`` jvspatial Node (in
app/models/nodes.py) has a 9-field shape; this Pydantic boundary mirrors
that shape and surfaces it on three REST endpoints:

    GET  /api/approvals                       (list — per-record permission filter)
    POST /api/approvals/{id}/approve          (run on approve)
    POST /api/approvals/{id}/reject           (mark rejected; emit policy.deny)

The Literal members ``actor_kind`` and ``action`` are validated at the
Pydantic boundary even though the persisted Node stores them as ``str``
(same idiom as ChangeEvent.actor_kind → Actor.kind in app/schemas/audit.py).

``status`` is a free-form ``str`` here even though the on-the-wire values
are constrained to a 4-tuple ``pending|approved|rejected|expired`` — keeping
it as ``str`` mirrors the persistence Node shape and defers Literal
enforcement to a future hardening phase.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from app.schemas.policy import PolicyAction
from app.schemas.provenance import ActorKind


class ApprovalResponse(BaseModel):
    """Wire shape for a single Approval row (locked 9-field + audit timestamps).

    All 12 fields visible on the wire so the UI can render actor + action +
    resource + payload + timestamps + decided_at/decider_id when status
    transitions out of 'pending'.
    """

    id: str
    actor_kind: ActorKind
    actor_id: str
    action: PolicyAction
    resource_kind: str
    resource_id: str
    payload: Dict[str, Any]
    policy_id: str
    created_at: str
    expires_at: str
    status: str
    decided_at: Optional[str] = None
    decider_id: Optional[str] = None

    model_config = {"extra": "forbid"}


class ApprovalListResponse(BaseModel):
    """List shape for GET /api/approvals.

    Pagination is optional in v1 — the executor returns the full filtered
    list on every call. ``next_cursor`` + ``has_more`` are present for
    forward-compat with the cursor-pagination helpers in
    ``app/services/pagination.py``.
    """

    approvals: List[ApprovalResponse]
    next_cursor: Optional[str] = None
    has_more: bool = False

    model_config = {"extra": "forbid"}


class ApprovalDecisionResponse(BaseModel):
    """Wire shape for POST /api/approvals/{id}/{approve,reject}.

    ``result`` is populated on the approve path with the exported node from
    the re-run (e.g. the created Entry's serialized payload). Reject leaves
    ``result`` as None — the rejection itself is the audit signal.
    """

    approval_id: str
    status: str  # "approved" | "rejected"
    result: Optional[Dict[str, Any]] = None

    model_config = {"extra": "forbid"}


class ApprovalRejectRequest(BaseModel):
    """Optional body for POST /api/approvals/{id}/reject.

    Body may be empty (``{}``) — ``reason`` is optional metadata that gets
    surfaced on the ``policy.deny`` ChangeEvent details payload.
    """

    reason: Optional[str] = None

    model_config = {"extra": "forbid"}
